"""Document text extraction and PII redaction helpers.

Moved verbatim (logic-wise) from the old ``app_local.py`` so the FastAPI backend
reuses the exact same, already-tested redaction behaviour for text, PDF and
DOCX. No functional changes versus the Gradio version.
"""

from __future__ import annotations

import logging
import os
import uuid

logger = logging.getLogger("privacy_filter.redaction")


def temp_output_path(input_path: str, ext: str) -> str:
    """Return a unique path under TEMP for a redacted output file.

    Falls back to the input file's directory when TEMP is unavailable.
    ``ext`` is the suffix including the leading dot (e.g. ``".pdf"``).
    """
    base = os.environ.get("TEMP", os.path.dirname(input_path))
    return os.path.join(base, f"redacted_{uuid.uuid4().hex}{ext}")


def extract_text_from_pdf(pdf_path: str) -> str | None:
    """Extract all text from a PDF, or ``None`` on failure."""
    try:
        import fitz

        doc = fitz.open(pdf_path)
        try:
            pages = [page.get_text() for page in doc]
        finally:
            doc.close()
        return "\n".join(pages)
    except Exception as e:
        logger.error("PDF read failed: %s", e)
    return None


def extract_text_from_docx(docx_path: str) -> str | None:
    """Extract all paragraph text from a DOCX, or ``None`` on failure."""
    try:
        from docx import Document

        doc = Document(docx_path)
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        logger.error("DOCX read failed: %s", e)
    return None


def redact_pdf(input_path: str, detected_spans) -> str:
    """Produce a redacted copy of the PDF and return its path."""
    import fitz

    out_path = temp_output_path(input_path, ".pdf")
    doc = fitz.open(input_path)
    try:
        for page in doc:
            for span in detected_spans:
                old_text = span.text
                new_text = span.placeholder
                if not old_text or old_text == new_text:
                    continue
                results = page.search_for(old_text)
                for rect in results:
                    page.add_redact_annot(
                        rect, text=new_text, fontsize=9,
                        fontname="helv", fill=(1, 1, 1), text_color=(0, 0, 0),
                        align=0,
                    )
            page.apply_redactions()
        doc.save(out_path)
    finally:
        doc.close()
    return out_path


def redact_docx(input_path: str, detected_spans) -> str:
    """Apply PII redactions to a DOCX while preserving run-level formatting.

    Replaces ``old_text`` with ``new_text`` inside each affected paragraph by
    splitting the original runs at match boundaries, so the surrounding runs
    keep their original fonts, sizes, bold/italic flags, etc.
    """
    from docx import Document

    out_path = temp_output_path(input_path, ".docx")
    doc = Document(input_path)
    for span in detected_spans:
        old_text = span.text
        new_text = span.placeholder
        if not old_text or old_text == new_text:
            continue
        for para in doc.paragraphs:
            if old_text not in para.text:
                continue
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
