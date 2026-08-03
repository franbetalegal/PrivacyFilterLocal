"""Tests for full-container DOCX traversal: tables, headers/footers, comments."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

docx = pytest.importorskip("docx")

from server import docx_ops, redaction


class _Span:
    def __init__(self, text: str, placeholder: str):
        self.start = 0   # unused for DOCX
        self.end = len(text)
        self.text = text
        self.placeholder = placeholder


def _build_doc(tmp_path: Path) -> Path:
    doc = docx.Document()
    doc.add_paragraph("En el cuerpo aparece Juan Garcia.")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Actor"
    table.rows[0].cells[1].text = "Juan Garcia"
    table.rows[1].cells[0].text = "DNI"
    table.rows[1].cells[1].text = "12345678Z"
    section = doc.sections[0]
    section.header.paragraphs[0].text = "Expediente 42/2024 · Juan Garcia"
    section.footer.paragraphs[0].text = "Pagina 1 · IBAN ES9121000418450200051332"
    out = tmp_path / "in.docx"
    doc.save(str(out))
    return out


def test_extract_finds_body_table_header_footer(tmp_path: Path):
    p = _build_doc(tmp_path)
    text = docx_ops.extract_text(str(p))
    assert text is not None
    for expected in ("En el cuerpo", "Juan Garcia", "12345678Z",
                     "Expediente 42/2024", "IBAN ES9121000418450200051332"):
        assert expected in text, f"missing {expected!r} in extracted text"


def test_redact_covers_body_and_table_and_header_and_footer(tmp_path: Path):
    p = _build_doc(tmp_path)
    spans = [
        _Span("Juan Garcia", "[NOMBRE_1]"),
        _Span("12345678Z", "[DNI_1]"),
        _Span("ES9121000418450200051332", "[IBAN_1]"),
    ]
    out_path = redaction.redact_docx(str(p), spans)
    text = docx_ops.extract_text(out_path)
    assert "Juan Garcia" not in text
    assert "12345678Z" not in text
    assert "ES9121000418450200051332" not in text
    assert "[NOMBRE_1]" in text
    assert "[DNI_1]" in text
    assert "[IBAN_1]" in text


def test_iter_all_paragraphs_visits_nested_tables(tmp_path: Path):
    doc = docx.Document()
    outer = doc.add_table(rows=1, cols=1)
    inner_cell = outer.rows[0].cells[0]
    nested = inner_cell.add_table(rows=1, cols=1)
    nested.rows[0].cells[0].text = "PII interna: 12345678Z"
    p = tmp_path / "nested.docx"
    doc.save(str(p))

    d = docx.Document(str(p))
    all_text = "\n".join(x.text for x in docx_ops.iter_all_paragraphs(d))
    assert "12345678Z" in all_text
