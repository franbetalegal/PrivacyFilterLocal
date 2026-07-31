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
"""
from __future__ import annotations

import io
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("privacy_filter.pdf_ops")


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


def _extract_page(page, page_idx: int, fitz_mod):
    """Return ``(text, [CharCoord, ...])`` for one page using rawdict.

    Falls back to :func:`_ocr_page` when the page has no text layer.
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
        return _ocr_page(page, page_idx, fitz_mod)
    return "".join(parts), coords


def _ocr_page(page, page_idx: int, fitz_mod):
    """OCR a page image with Tesseract and produce a compatible char map.

    Returns ``("", [])`` (and logs a warning) if Tesseract is not installed on
    the host, so extraction never crashes on a scanned page.
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


def extract_with_map(pdf_path: str) -> Optional[tuple[str, list[CharCoord]]]:
    """Extract text + char coordinates from every page. ``None`` on failure."""
    try:
        import fitz
    except ImportError as exc:
        logger.error("PyMuPDF is not installed: %s", exc)
        return None
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("PDF open failed: %s", exc)
        return None
    try:
        all_parts: list[str] = []
        all_coords: list[CharCoord] = []
        for page_idx, page in enumerate(doc):
            page_text, page_coords = _extract_page(page, page_idx, fitz)
            all_parts.append(page_text)
            all_coords.extend(page_coords)
            # Page separator (matches ``\n`` join semantics of the old extractor).
            all_parts.append("\n")
            all_coords.append(CharCoord(page_idx, None))
        text = "".join(all_parts)
        assert len(text) == len(all_coords), "coord map / text length mismatch"
        return text, all_coords
    finally:
        doc.close()


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
