"""Document text extraction and PII redaction helpers.

Public entry points (kept stable for :mod:`server.main`):

    - :func:`extract_text_from_pdf`  → flat string, or ``None`` on failure.
    - :func:`extract_text_from_docx` → flat string, or ``None`` on failure.
    - :func:`redact_pdf`             → path to a redacted copy.
    - :func:`redact_docx`            → path to a redacted copy.

Since Phase 4, extraction covers every OOXML text container (tables,
headers/footers, text boxes, comments) and every PDF page — falling back to
OCR (Tesseract, ``lang=spa+eng``) when a page has no text layer. Redaction
translates the pipeline's character-offset spans back into rectangles via a
per-char coordinate map, so line-broken or hyphenated PII is still covered.
"""

from __future__ import annotations

import logging
import os
import uuid

from server import docx_ops, pdf_ops

logger = logging.getLogger("privacy_filter.redaction")


def temp_output_path(input_path: str, ext: str) -> str:
    """Return a unique path under TEMP for a redacted output file.

    Falls back to the input file's directory when TEMP is unavailable.
    ``ext`` is the suffix including the leading dot (e.g. ``".pdf"``).
    """
    base = os.environ.get("TEMP", os.path.dirname(input_path))
    return os.path.join(base, f"redacted_{uuid.uuid4().hex}{ext}")


def extract_text_from_pdf(pdf_path: str) -> str | None:
    """Extract all text from a PDF (OCRs pages that lack a text layer)."""
    return pdf_ops.extract_text(pdf_path)


def extract_text_from_docx(docx_path: str) -> str | None:
    """Extract text from every container in the DOCX."""
    return docx_ops.extract_text(docx_path)


def redact_pdf(input_path: str, detected_spans) -> str:
    """Produce a redacted copy of the PDF using offset-based coordinate lookup."""
    out_path = temp_output_path(input_path, ".pdf")
    pdf_ops.redact_by_offsets(input_path, out_path, detected_spans)
    return out_path


def redact_docx(input_path: str, detected_spans) -> str:
    """Apply PII redactions to every text container in the DOCX.

    Preserves the run-level formatting inside each container by splitting the
    matching runs at the placeholder boundary (see
    :func:`_replace_text_in_paragraph`).
    """
    from docx import Document

    out_path = temp_output_path(input_path, ".docx")
    doc = Document(input_path)
    for span in detected_spans:
        old_text = span.text
        new_text = span.placeholder
        if not old_text or old_text == new_text:
            continue
        for para in docx_ops.iter_all_paragraphs(doc):
            if old_text in para.text:
                _replace_text_in_paragraph(para, old_text, new_text)
    doc.save(out_path)
    return out_path


def _replace_text_in_paragraph(paragraph, old_text, new_text):
    """Replace ``old_text`` with ``new_text`` in ``paragraph`` run-by-run.

    Splits runs at the first match boundary so the resulting placeholder sits
    in its own run; the run-level formatting of the surrounding text is
    preserved.
    """
    full_text = paragraph.text
    start = full_text.find(old_text)
    if start < 0:
        return
    end = start + len(old_text)

    # Build the new sequence of (text, run_template) for the paragraph.
    # Each overlapping run produces a leading slice (if any) and a trailing
    # slice (if any); the placeholder is inserted exactly once for the whole
    # match even when it spans multiple runs.
    segments: list[tuple[str, object]] = []
    cursor = 0
    placeholder_inserted = False
    for run in paragraph.runs:
        run_text = run.text
        if not run_text:
            continue
        run_start = cursor
        run_end = cursor + len(run_text)
        if run_end <= start or run_start >= end:
            segments.append((run_text, run))
        else:
            if run_start < start:
                segments.append((run_text[: start - run_start], run))
            if not placeholder_inserted:
                segments.append((new_text, None))
                placeholder_inserted = True
            if run_end > end:
                segments.append((run_text[end - run_start :], run))
        cursor = run_end

    if not segments:
        return

    # Find the first run with content to use as the formatting template for
    # the placeholder.
    template_for_placeholder = None
    for run in paragraph.runs:
        if run.text:
            template_for_placeholder = run
            break

    # Clear all original runs.
    for run in paragraph.runs:
        run.text = ""

    # Reuse the first run for the first segment, then add new runs for the rest.
    first_text, first_template = segments[0]
    paragraph.runs[0].text = first_text
    if first_template is not None:
        _copy_font(first_template.font, paragraph.runs[0].font)

    for text, template in segments[1:]:
        new_run = paragraph.add_run(text)
        if template is not None:
            _copy_font(template.font, new_run.font)
        elif template_for_placeholder is not None:
            _copy_font(template_for_placeholder.font, new_run.font)


def _copy_font(src_font, dst_font):
    """Copy font attributes from ``src_font`` to ``dst_font`` if set."""
    if src_font.name:
        dst_font.name = src_font.name
    if src_font.size:
        dst_font.size = src_font.size
    if src_font.bold is not None:
        dst_font.bold = src_font.bold
    if src_font.italic is not None:
        dst_font.italic = src_font.italic
