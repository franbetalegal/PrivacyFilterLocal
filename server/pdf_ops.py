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
        dpi = int(os.environ.get("PF_OCR_DPI", "300"))
        lang = os.environ.get("PF_OCR_LANG", "spa+eng")
        os.environ["OMP_THREAD_LIMIT"] = str(max(1, ocr_threads))
        pix = page.get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
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
    for i, word in enumerate(data["text"]):
        if not word:
            continue
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
        parts.append(" ")
        coords.append(CharCoord(page_idx, None, from_ocr=True))
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


def extract_with_map(pdf_path: str) -> Optional[tuple[str, list[CharCoord]]]:
    """Extract text + char coordinates from every page. ``None`` on failure.

    Pages are split into contiguous ranges and extracted in parallel across
    OS processes when the document has enough pages to be worth it (see
    :data:`_MIN_PAGES_FOR_PARALLEL_EXTRACT`) and the host has spare cores
    (see :mod:`server.concurrency`) — otherwise this runs sequentially in the
    current process, identically to before parallel extraction existed.
    """
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

    if workers <= 1 or ocr_pages < _MIN_OCR_PAGES_FOR_PARALLEL:
        # Sequential: one process, one page at a time. Tesseract may use the
        # full core budget for each page's own OCR since nothing else is
        # running concurrently. Text-layer PDFs always take this path — they're
        # fast enough that process-spawn overhead would only slow them down.
        chunk = _extract_page_range(
            pdf_path, 0, num_pages, fitz, ocr_threads=concurrency.worker_count()
        )
        for page_idx, page_text, page_coords in chunk:
            all_parts.append(page_text)
            all_coords.extend(page_coords)
            all_parts.append("\n")
            all_coords.append(CharCoord(page_idx, None))
    else:
        ranges = _split_ranges(num_pages, workers)
        pool = _get_pdf_pool(len(ranges))
        futures = [
            pool.submit(_extract_page_range_worker, pdf_path, start, end)
            for start, end in ranges
        ]
        chunks = [f.result() for f in futures]
        for chunk in chunks:
            for page_idx, page_text, coord_tuples in chunk:
                all_parts.append(page_text)
                all_coords.extend(
                    CharCoord(pi, None if rect is None else fitz.Rect(*rect), from_ocr)
                    for pi, rect, from_ocr in coord_tuples
                )
                all_parts.append("\n")
                all_coords.append(CharCoord(page_idx, None))

    text = "".join(all_parts)
    assert len(text) == len(all_coords), "coord map / text length mismatch"
    return text, all_coords


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
        doc.save(output_path)
    finally:
        doc.close()
