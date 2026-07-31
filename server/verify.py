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


def _extract(path: str, ext: str) -> str:
    """Return the text of the redacted file, best-effort."""
    ext = ext.lower()
    if ext == ".pdf":
        from server import pdf_ops
        text = pdf_ops.extract_text(path)
        return text or ""
    if ext == ".docx":
        from server import docx_ops
        text = docx_ops.extract_text(path)
        return text or ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Could not read %s for verification: %s", path, exc)
        return ""


def find_leaks(output_path: str, ext: str, detected_spans) -> list[str]:
    """Return the sorted list of original PII strings still present in the output.

    Duplicates are collapsed; the list is intended for a UI warning banner,
    not for downstream automation.
    """
    text = _extract(output_path, ext)
    if not text:
        return []
    leaked: set[str] = set()
    for span in detected_spans:
        original = getattr(span, "text", None)
        if not original:
            continue
        if original == getattr(span, "placeholder", ""):
            continue
        if original in text:
            leaked.add(original)
    return sorted(leaked)
