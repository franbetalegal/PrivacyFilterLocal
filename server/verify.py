"""Post-redaction verification: prove no detected PII survives in the output.

A detection layer can still leak PII into the redacted file when the
underlying renderer fails to tap it: PyMuPDF's ``search_for`` can miss
line-broken words; python-docx's basic run-splitter can miss text in a
container we didn't visit. This module re-extracts the output document and
reports which original PII strings are still present, so the API can surface
that as a machine-readable warning instead of silently shipping a broken
anonymization.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("privacy_filter.verify")


def _extract_pdf_text_layer(path: str) -> str:
    """Text-layer-only extraction of a PDF, no OCR.

    Verification only cares whether a *string* survived: if the output has a
    text layer (any placeholder text inserted by add_redact_annot lives there,
    plus any text-layer content from the original), we can read it directly
    with PyMuPDF and skip the OCR round-trip that :func:`server.pdf_ops
    .extract_text` would trigger on a scanned page. That saves the third full
    OCR pass on documents where extraction is the bottleneck.
    """
    try:
        import fitz
    except ImportError as exc:
        logger.warning("PyMuPDF unavailable during verify: %s", exc)
        return ""
    try:
        doc = fitz.open(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Verify open failed on %s: %s", path, exc)
        return ""
    try:
        return "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()


def _extract(path: str, ext: str) -> str:
    """Return the text of the redacted file, best-effort."""
    ext = ext.lower()
    if ext == ".pdf":
        return _extract_pdf_text_layer(path)
    if ext == ".docx":
        from server import docx_ops
        text = docx_ops.extract_text(path)
        return text or ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Could not read %s for verification: %s", path, exc)
        return ""


def can_verify(output_path: str, ext: str) -> bool:
    """True when a leak check on this output can actually detect anything.

    A redacted **scanned** PDF has no text layer beyond the placeholders that
    ``add_redact_annot`` inserted, so a text-layer read finds nothing and
    reports "no leaks" for a document it never inspected. Measured A/B: on a
    text-layer PDF the check flagged 156 of 156 deliberately un-redacted
    strings; on the same document as a 200-dpi scan it flagged 0 of 137.

    Callers must surface this as "could not verify" rather than as "clean".
    """
    if ext.lower() != ".pdf":
        return True
    return bool(_extract_pdf_text_layer(output_path).strip())


def find_leaks(output_path: str, ext: str, detected_spans) -> list[str]:
    """Return the sorted list of original PII strings still present in the output.

    Duplicates are collapsed; the list is intended for a UI warning banner,
    not for downstream automation. An empty list means "nothing found", which
    is only meaningful when :func:`can_verify` is true — check it first.
    """
    text = _extract(output_path, ext)
    if not text:
        return []
    # Scan once per *distinct* string: a document repeats the same name dozens
    # of times, and the return value is a set anyway.
    candidates = {
        original
        for span in detected_spans
        if (original := getattr(span, "text", None))
        and original != getattr(span, "placeholder", "")
    }
    return sorted(c for c in candidates if c in text)
