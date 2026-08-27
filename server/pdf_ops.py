"""PDF text extraction with a per-character coordinate map, and offset-based
redaction.

The pipeline in :mod:`server.pipeline` returns character-offset spans against
the flat text of the document. To redact a PDF reliably we cannot simply search
for the string on the page: line breaks, hyphenation, ligatures and CID
mappings routinely make the extracted string *un-searchable* on the very page
it came from. Instead, we remember where every extracted character sits
(``page_index`` + rectangle), then translate any offset range into the set of
rectangles it covers.

Scanned pages with no text layer are OCRed with Tesseract (``pytesseract``) so
they can be redacted through the same offset-based path. Tesseract is an
optional dependency: if it or its binary is missing, OCR is skipped and a
warning is logged.

Multi-page documents are extracted in parallel across OS processes (one page
range per worker), bounded by :func:`server.concurrency.worker_count` so a
single anonymization job never uses every core on the host. Each worker
process is also capped to a single internal Tesseract thread
(``OMP_THREAD_LIMIT``) so N parallel workers don't each try to use all cores
for their own OCR call — see :func:`_ocr_page`.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import threading
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Optional

from server import concurrency

logger = logging.getLogger("privacy_filter.pdf_ops")

# Parallel extraction only pays off for OCR (scanned) pages. Extracting a
# text-layer PDF is fast enough sequentially that the process-spawn overhead
# (spawn re-imports the whole ``server`` package, torch included) would make
# the parallel path *slower*. So we parallelize only when at least this many
# pages actually need OCR.
_MIN_OCR_PAGES_FOR_PARALLEL = 3

# Never rasterise below this, even when the embedded image is coarser:
# Tesseract degrades sharply on very low-resolution input.
_MIN_OCR_DPI = 150


def _ocr_line_breaks_enabled() -> bool:
    """Whether OCRed pages carry their line breaks (``PF_OCR_LINE_BREAKS``).

    On by default. One line of the page becomes one line of text, which is what
    lets the analyzer run NER per line as well as per paragraph (see
    :func:`server.ner_es._iter_segments`) — the pass that catches a name sitting
    alone on a header line.

    Measured on a real 4-page scanned court order, comparing the same document
    joined by spaces against the same document with line breaks:

    * the person named on the "MENOR: <name>" header line was found only with
      line breaks, in every operating point. Joined by spaces that line sits in
      a header soup ("... Expediente 4242/2016 <field label> MENOR: <name> <next
      heading> ...") and spaCy proposes nothing at all for the region;
    * the addressee's name came out whole ("a two-word given name plus surname") instead of as
      two wrong fragments, and the address came out whole too;
    * the case numbers were LOST, because the transformer had been reading them
      out of the surrounding line. That is repaired at the source instead:
      ``ES_CASE_NUMBER`` in :mod:`server.recognizers_es` now matches them
      deterministically, so a number that identifies a case no longer depends
      on how the OCR laid out the page.

    Block and paragraph boundaries deliberately do NOT become blank lines.
    Tesseract splits a noisy scan into blocks freely — a watermark or a column
    becomes its own block — and a blank line there would cut the context both
    models depend on.

    The environment variable stays as the escape hatch: set it to 0 to get the
    previous whole-page-as-one-line behaviour on a document where this reads
    worse.
    """
    return os.environ.get("PF_OCR_LINE_BREAKS", "1").strip() not in ("", "0")


@dataclass(frozen=True)
class CharCoord:
    """Where the i-th character of the extracted text lives on the page.

    ``rect`` is ``None`` for line/block/page separators — synthetic characters
    inserted between extracted runs to keep the flat text readable — because
    those don't correspond to any glyph on the page and shouldn't be redacted.
    """

    page_index: int
    rect: object            # fitz.Rect | None; type kept loose to defer import
    from_ocr: bool = False


def _extract_page(page, page_idx: int, fitz_mod, *, ocr_threads: int = 1):
    """Return ``(text, [CharCoord, ...])`` for one page using rawdict.

    Falls back to :func:`_ocr_page` when the page has no text layer.
    ``ocr_threads`` bounds Tesseract's own internal threading if OCR runs —
    see :func:`_ocr_page` for why this matters when pages are processed
    concurrently.
    """
    # Cheap test first. ``rawdict`` costs ~100 ms on an image-only page because
    # it materialises the embedded image bytes into the returned dict — data we
    # then skip via the ``type != 0`` filter below. ``get_text("text")`` answers
    # "is there a text layer?" in ~0.08 ms, so probing with it saves ~23 s of
    # CPU on a 232-page scan. Behaviour is identical: with no text layer the
    # loop below sets no characters and we'd fall through to OCR anyway.
    if not page.get_text("text").strip():
        return _ocr_page(page, page_idx, fitz_mod, ocr_threads=ocr_threads)

    raw = page.get_text("rawdict")
    parts: list[str] = []
    coords: list[CharCoord] = []
    found_char = False
    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # skip image / form blocks
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    parts.append(ch["c"])
                    coords.append(CharCoord(page_idx, fitz_mod.Rect(ch["bbox"])))
                    found_char = True
            # Line break: no glyph, no rect.
            parts.append("\n")
            coords.append(CharCoord(page_idx, None))
        parts.append("\n")
        coords.append(CharCoord(page_idx, None))
    if not found_char:
        return _ocr_page(page, page_idx, fitz_mod, ocr_threads=ocr_threads)
    return "".join(parts), coords


def _native_dpi(page) -> float:
    """Resolution of the largest image embedded in ``page``, or 0.0 if none.

    Used to avoid upsampling: rasterising a 200-dpi scan at 300 dpi produces
    50% more pixels to encode and recognise while interpolating no new detail.
    """
    try:
        info = page.get_image_info()
    except Exception:  # noqa: BLE001 — never let a probe break extraction
        return 0.0
    best = 0.0
    page_w = page.rect.width or 1.0
    page_h = page.rect.height or 1.0
    for img in info:
        w = img.get("width") or 0
        h = img.get("height") or 0
        if not w or not h:
            continue
        # 72 pt per inch; take the smaller axis so we never overstate.
        best = max(best, min(w * 72.0 / page_w, h * 72.0 / page_h))
    return best


def _effective_dpi(page) -> int:
    """Raster DPI for this page: the configured value, capped at native.

    ``PF_OCR_DPI`` (default 200) is the ceiling. Measured on synthetic scans,
    200 dpi beat 300 dpi on validated-identifier recall (92.9% vs 92.2%,
    McNemar p=0.024) *and* was faster — 300 dpi mostly interpolates noise. But
    a document already stored below the ceiling regressed when it was
    upsampled, so we clamp to the page's own resolution and keep a floor so a
    very low-resolution scan is not made worse.
    """
    ceiling = int(os.environ.get("PF_OCR_DPI", "200"))
    if os.environ.get("PF_OCR_CLAMP_NATIVE", "1").strip() == "0":
        return ceiling
    native = _native_dpi(page)
    if native <= 0:
        return ceiling
    return max(_MIN_OCR_DPI, int(min(ceiling, native)))


def _ocr_page(page, page_idx: int, fitz_mod, *, ocr_threads: int = 1):
    """OCR a page image with Tesseract and produce a compatible char map.

    Returns ``("", [])`` (and logs a warning) if Tesseract is not installed on
    the host, so extraction never crashes on a scanned page.

    ``ocr_threads`` caps Tesseract's internal OpenMP thread count
    (``OMP_THREAD_LIMIT``). When pages are extracted sequentially in one
    process, this can be the full CPU budget (one page OCRs at a time, so it
    may use every core we're allowed). When pages are extracted in parallel
    across N worker processes, each worker passes ``ocr_threads=1`` so the N
    concurrent Tesseract calls don't each also try to use every core — total
    OS-level threads then stays at N, matching the reserved-core budget in
    :mod:`server.concurrency`.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning(
            "Page %d has no text layer and OCR deps are missing "
            "(pip install pytesseract Pillow, plus the tesseract binary + spa data).",
            page_idx,
        )
        return "", []

    try:
        dpi = _effective_dpi(page)
        lang = os.environ.get("PF_OCR_LANG", "spa+cat+eng")
        os.environ["OMP_THREAD_LIMIT"] = str(max(1, ocr_threads))
        # Grayscale, and hand the raw samples straight to PIL. Tesseract
        # converts to grayscale internally anyway, so rendering 3 channels
        # produces two thirds of the pixels for nothing; and going through
        # ``tobytes("png")`` makes the bitmap cross three codec passes (MuPDF
        # encodes PNG → PIL decodes → pytesseract re-encodes, because it keys
        # off ``image.format``). Measured together: −18% per page, with
        # byte-identical extracted text and byte-identical rectangles.
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz_mod.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        data = pytesseract.image_to_data(
            img, lang=lang, output_type=pytesseract.Output.DICT
        )
    except (pytesseract.TesseractNotFoundError, RuntimeError) as exc:
        logger.warning("OCR skipped on page %d: %s", page_idx, exc)
        return "", []

    sx = page.rect.width / pix.width if pix.width else 1.0
    sy = page.rect.height / pix.height if pix.height else 1.0
    parts: list[str] = []
    coords: list[CharCoord] = []
    line_breaks = _ocr_line_breaks_enabled()
    previous_line: tuple[int, int, int] | None = None
    for i, word in enumerate(data["text"]):
        if not word:
            continue
        separator = " " if previous_line is not None else ""
        if line_breaks:
            # Tesseract reports which block, paragraph and line each word
            # belongs to, so a line break is read from it rather than guessed.
            line_id = (
                data["block_num"][i], data["par_num"][i], data["line_num"][i]
            )
            if previous_line is not None and line_id != previous_line:
                separator = "\n"
            previous_line = line_id
        else:
            previous_line = ()
        for ch in separator:
            parts.append(ch)
            coords.append(CharCoord(page_idx, None, from_ocr=True))
        left = data["left"][i] * sx
        top = data["top"][i] * sy
        width = data["width"][i] * sx
        height = data["height"][i] * sy
        # Approximate per-char rects by uniform division within the word rect.
        word_len = len(word)
        cw = (width / word_len) if word_len else 0.0
        for j, ch in enumerate(word):
            x0 = left + j * cw
            x1 = left + (j + 1) * cw
            parts.append(ch)
            coords.append(CharCoord(
                page_index=page_idx,
                rect=fitz_mod.Rect(x0, top, x1, top + height),
                from_ocr=True,
            ))
    parts.append("\n")
    coords.append(CharCoord(page_idx, None, from_ocr=True))
    return "".join(parts), coords


def _split_ranges(n: int, workers: int) -> list[tuple[int, int]]:
    """Split ``n`` items into up to ``workers`` contiguous, balanced ranges.

    Never returns more ranges than items (a 2-page doc with workers=8 yields
    2 ranges, not 8 near-empty ones), and never fewer than 1.
    """
    workers = max(1, min(workers, n))
    base, remainder = divmod(n, workers)
    ranges: list[tuple[int, int]] = []
    start = 0
    for i in range(workers):
        size = base + (1 if i < remainder else 0)
        if size == 0:
            continue
        ranges.append((start, start + size))
        start += size
    return ranges


def _extract_page_range(pdf_path: str, start: int, end: int, fitz_mod, *, ocr_threads: int = 1):
    """Extract text+coords for pages ``[start, end)``, opening the doc once."""
    doc = fitz_mod.open(pdf_path)
    try:
        results = []
        for page_idx in range(start, end):
            page = doc[page_idx]
            text, coords = _extract_page(page, page_idx, fitz_mod, ocr_threads=ocr_threads)
            results.append((page_idx, text, coords))
        return results
    finally:
        doc.close()


def _extract_page_range_worker(pdf_path: str, start: int, end: int):
    """``ProcessPoolExecutor`` entry point: re-imports fitz in the worker
    process and returns plain-tuple coordinates (a ``fitz.Rect`` isn't a type
    we want to rely on being picklable across the process boundary).

    Runs with ``ocr_threads=1`` — see :func:`_ocr_page` for why concurrent
    workers must not each also multithread their own Tesseract call.
    """
    import fitz
    chunk = _extract_page_range(pdf_path, start, end, fitz, ocr_threads=1)
    return [
        (
            page_idx,
            text,
            [
                (
                    c.page_index,
                    None if c.rect is None else (c.rect.x0, c.rect.y0, c.rect.x1, c.rect.y1),
                    c.from_ocr,
                )
                for c in coords
            ],
        )
        for page_idx, text, coords in chunk
    ]


_pdf_pool_lock = threading.Lock()
_pdf_pool: Optional[ProcessPoolExecutor] = None
_pdf_pool_size = 0

# Extraction cache keyed by content digest + size. A single upload is extracted
# twice per request (detection, then the rebuild inside redact_by_offsets); the
# leak check no longer comes through here, since server/verify.py reads the
# output's text layer directly. Keying on content rather than path also makes
# the /api/redact-file/apply re-upload a cache hit instead of a second OCR pass.
#
# Small on purpose (2 entries): the API only handles one active upload at a
# time in practice, and stale entries hold no PII beyond what the caller
# already has in memory for the same request. Not thread-safe by design —
# concurrent requests would each be over their own upload path.
_EXTRACT_CACHE_SIZE = 2
_extract_cache: list[tuple[tuple[str, int, int], tuple[str, list[CharCoord]]]] = []
_extract_cache_lock = threading.Lock()


def _cache_key(pdf_path: str) -> Optional[tuple[str, int]]:
    """Identify the *content*, not the path.

    Keying on the path meant the /api/redact-file/apply endpoint — which
    re-uploads identical bytes to a new mkstemp path — always missed and paid
    for a second full OCR pass. A size + digest key makes that a hit.
    """
    try:
        st = os.stat(pdf_path)
        with open(pdf_path, "rb") as fh:
            digest = hashlib.blake2b(fh.read(), digest_size=16).hexdigest()
    except OSError:
        return None
    return (digest, st.st_size)


def _cache_get(key: tuple) -> Optional[tuple[str, list[CharCoord]]]:
    with _extract_cache_lock:
        for k, v in _extract_cache:
            if k == key:
                return v
    return None


def _cache_put(key: tuple, value: tuple[str, list[CharCoord]]) -> None:
    with _extract_cache_lock:
        _extract_cache[:] = [
            (k, v) for k, v in _extract_cache if k != key
        ]
        _extract_cache.append((key, value))
        del _extract_cache[:-_EXTRACT_CACHE_SIZE]


def clear_extract_cache() -> None:
    """Drop every cached extraction; call this after the request is done so
    PII text doesn't linger in memory longer than needed."""
    with _extract_cache_lock:
        _extract_cache.clear()


def _get_pdf_pool(size: int) -> ProcessPoolExecutor:
    """Return a page-extraction process pool sized at least ``size``.

    Reused across calls — a single upload re-extracts its PDF more than once
    (initial detection, rebuilding the offset map for redaction, then
    post-redaction leak verification), so a persistent pool avoids paying
    process-spawn overhead on every one of those passes. Recreated only if a
    larger pool is requested than the one already running.
    """
    global _pdf_pool, _pdf_pool_size
    with _pdf_pool_lock:
        if _pdf_pool is None or size > _pdf_pool_size:
            if _pdf_pool is not None:
                _pdf_pool.shutdown(wait=False)
            _pdf_pool = ProcessPoolExecutor(max_workers=size)
            _pdf_pool_size = size
        return _pdf_pool


def _shutdown_pdf_pool() -> None:
    """Drop the page-extraction pool so the next call builds a fresh one.

    A broken pool stays broken: every later submit raises immediately. Without
    this, one failed document would push the whole process onto the sequential
    path for good.
    """
    global _pdf_pool, _pdf_pool_size
    with _pdf_pool_lock:
        if _pdf_pool is not None:
            _pdf_pool.shutdown(wait=False)
        _pdf_pool = None
        _pdf_pool_size = 0


def extract_with_map(pdf_path: str) -> Optional[tuple[str, list[CharCoord]]]:
    """Extract text + char coordinates from every page. ``None`` on failure.

    Pages are split into contiguous ranges and extracted in parallel across
    OS processes when the document has enough pages to be worth it (see
    :data:`_MIN_OCR_PAGES_FOR_PARALLEL`) and the host has spare cores (see
    :mod:`server.concurrency`) — otherwise this runs sequentially in the
    current process, identically to before parallel extraction existed.

    Results are cached by ``(path, size, mtime)`` so a single request that
    extracts the same upload multiple times (detection → redaction rebuild →
    leak verification) only pays the OCR cost once. See
    :func:`clear_extract_cache` for the request-end cleanup.
    """
    key = _cache_key(pdf_path)
    if key is not None:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    try:
        import fitz
    except ImportError as exc:
        logger.error("PyMuPDF is not installed: %s", exc)
        return None
    try:
        probe = fitz.open(pdf_path)
        num_pages = len(probe)
        # Cheap classification: a page with no extractable text will need OCR.
        # This read is far cheaper than the OCR it lets us decide to parallelize.
        ocr_pages = 0
        for page in probe:
            if not page.get_text("text").strip():
                ocr_pages += 1
        probe.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("PDF open failed: %s", exc)
        return None

    if num_pages == 0:
        return "", []

    workers = min(concurrency.worker_count(), num_pages)
    all_parts: list[str] = []
    all_coords: list[CharCoord] = []

    def _sequential() -> list:
        # One process, one page at a time. Tesseract may use the full core
        # budget for each page's own OCR since nothing else is running
        # concurrently. Text-layer PDFs always take this path — they're fast
        # enough that process-spawn overhead would only slow them down.
        logger.info(
            "PDF extract sequential: %d pages (%d need OCR), workers=%d",
            num_pages, ocr_pages, concurrency.worker_count(),
        )
        return _extract_page_range(
            pdf_path, 0, num_pages, fitz, ocr_threads=concurrency.worker_count()
        )

    chunks: list[list] = []
    if workers <= 1 or ocr_pages < _MIN_OCR_PAGES_FOR_PARALLEL:
        chunks = [_sequential()]
    else:
        ranges = _split_ranges(num_pages, workers)
        logger.info(
            "PDF extract parallel: %d pages (%d OCR) → %d workers, ranges=%s",
            num_pages, ocr_pages, len(ranges), ranges,
        )
        try:
            pool = _get_pdf_pool(len(ranges))
            futures = [
                pool.submit(_extract_page_range_worker, pdf_path, start, end)
                for start, end in ranges
            ]
            chunks = [f.result() for f in futures]
        except Exception as exc:  # noqa: BLE001
            # Parallel extraction is an optimisation; losing it must not lose
            # the document. A worker dying takes the whole pool down with it
            # (BrokenProcessPool) and every pending page with it — reproduced on
            # a real 4-page scanned court order, where each spawned worker
            # re-imports the server package, torch included, and the machine
            # could not carry four of those at once. The pages themselves
            # extract fine one at a time.
            logger.warning(
                "Parallel PDF extraction failed (%s: %s); "
                "falling back to sequential.", type(exc).__name__, exc,
            )
            _shutdown_pdf_pool()
            chunks = [_sequential()]

    # Workers hand back plain tuples (a fitz.Rect is not something to rely on
    # pickling across a process boundary); the in-process path hands back
    # CharCoord directly. Accept both so the fallback needs no special casing.
    for chunk in chunks:
        for page_idx, page_text, page_coords in chunk:
            all_parts.append(page_text)
            all_coords.extend(
                coord if isinstance(coord, CharCoord) else CharCoord(
                    coord[0],
                    None if coord[1] is None else fitz.Rect(*coord[1]),
                    coord[2],
                )
                for coord in page_coords
            )
            all_parts.append("\n")
            all_coords.append(CharCoord(page_idx, None))

    text = "".join(all_parts)
    assert len(text) == len(all_coords), "coord map / text length mismatch"
    result = (text, all_coords)
    if key is not None:
        _cache_put(key, result)
    return result


def extract_text(pdf_path: str) -> Optional[str]:
    """Public: just the text (map dropped). ``None`` on failure."""
    result = extract_with_map(pdf_path)
    return None if result is None else result[0]


def _merge_rects_by_line(rects, y_tolerance: float = 1.0):
    """Group char rects sharing a baseline into one line-wide rect each."""
    import fitz
    if not rects:
        return []
    lines: dict[int, list] = {}
    for r in rects:
        key = int(round(r.y0 / y_tolerance))
        lines.setdefault(key, []).append(r)
    merged = []
    for group in lines.values():
        group.sort(key=lambda r: r.x0)
        combined = fitz.Rect(group[0])
        for r in group[1:]:
            combined |= r
        merged.append(combined)
    return merged


def redact_by_offsets(input_path: str, output_path: str, detected_spans) -> None:
    """Apply redactions to a PDF using each span's ``(start, end)`` offsets.

    The map used to translate offsets → rectangles is rebuilt from the same
    input file so it is identical to the one seen by the pipeline; assuming
    :func:`extract_text` is deterministic on a given file, offsets from spans
    detected on that extraction remain valid here.
    """
    result = extract_with_map(input_path)
    if result is None:
        raise RuntimeError(f"Could not re-extract PDF for redaction: {input_path}")
    text, coords = result

    import fitz
    doc = fitz.open(input_path)
    try:
        # Group rectangles by page.
        annotations: defaultdict[int, list[tuple[object, str]]] = defaultdict(list)
        for span in detected_spans:
            start = int(span.start)
            end = int(span.end)
            if start < 0 or end > len(coords) or start >= end:
                continue
            per_page: dict[int, list] = {}
            for i in range(start, end):
                c = coords[i]
                if c.rect is None:
                    continue
                per_page.setdefault(c.page_index, []).append(c.rect)
            for page_idx, rects in per_page.items():
                for rect in _merge_rects_by_line(rects):
                    annotations[page_idx].append((rect, span.placeholder))

        for page_idx, annots in annotations.items():
            page = doc[page_idx]
            for rect, placeholder in annots:
                page.add_redact_annot(
                    rect,
                    text=placeholder,
                    fontsize=9,
                    fontname="helv",
                    fill=(1, 1, 1),
                    text_color=(0, 0, 0),
                    align=0,
                )
            page.apply_redactions()
        # ``apply_redactions`` re-encodes every touched page, and on a scanned
        # document that means a full-page raster per page. PyMuPDF's default
        # save writes those uncompressed: measured on a 24-page scan, an 8.3 MB
        # input produced a 286.9 MB output (~12 MB/page, i.e. ~2.8 GB for a
        # 232-page file). deflate + garbage collection brings that to 7.8 MB
        # (~37x smaller) for ~1.3 s per 24 pages, and makes the leak-check
        # re-read about 10x faster.
        doc.save(output_path, deflate=True, garbage=3)
    finally:
        doc.close()
