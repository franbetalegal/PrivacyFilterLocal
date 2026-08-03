"""Tests for offset-based PDF extraction and redaction.

Uses PyMuPDF's own writer to synthesize input PDFs on the fly, so no fixtures
are required and the tests exercise the real extract → offsets → redact loop.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fitz = pytest.importorskip("fitz")

from server import pdf_ops


def _write_pdf(tmp_path: Path, lines: list[str], filename: str = "in.pdf") -> Path:
    """Create a single-page PDF containing ``lines`` (one per line)."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)   # A4
    y = 72.0
    for line in lines:
        page.insert_text((72, y), line, fontsize=12, fontname="helv")
        y += 18.0
    out = tmp_path / filename
    doc.save(str(out))
    doc.close()
    return out


class _Span:
    """Minimal duck-typed span for the redactor."""

    def __init__(self, start: int, end: int, text: str, placeholder: str):
        self.start = start
        self.end = end
        self.text = text
        self.placeholder = placeholder


def test_extract_with_map_returns_text_and_matching_length(tmp_path: Path):
    pdf = _write_pdf(tmp_path, ["Hello world"])
    result = pdf_ops.extract_with_map(str(pdf))
    assert result is not None
    text, coords = result
    assert "Hello world" in text
    assert len(text) == len(coords)
    # Chars in "Hello world" should have real rectangles; separators have None.
    h_index = text.index("H")
    assert coords[h_index].rect is not None
    assert coords[h_index].page_index == 0


def test_extract_with_map_missing_file_returns_none(tmp_path: Path):
    assert pdf_ops.extract_with_map(str(tmp_path / "nope.pdf")) is None


def test_redact_by_offsets_removes_target_substring(tmp_path: Path):
    pdf = _write_pdf(tmp_path, ["Nombre: Juan Garcia Perez, DNI 12345678Z"])
    text, _ = pdf_ops.extract_with_map(str(pdf))
    start = text.index("Juan Garcia Perez")
    end = start + len("Juan Garcia Perez")

    out = tmp_path / "out.pdf"
    pdf_ops.redact_by_offsets(
        str(pdf), str(out),
        [_Span(start, end, "Juan Garcia Perez", "[NOMBRE_1]")],
    )
    result_text = pdf_ops.extract_text(str(out))
    assert "Juan Garcia Perez" not in result_text
    assert "[NOMBRE_1]" in result_text or "NOMBRE_1" in result_text


def test_redact_handles_span_broken_across_lines(tmp_path: Path):
    # The DNI is split across two lines — text extraction gives "...12345678\n...Z...".
    pdf = _write_pdf(tmp_path, [
        "El interesado, con DNI 12345678",
        "Z, comparece ante el juzgado.",
    ])
    text, _ = pdf_ops.extract_with_map(str(pdf))
    # Locate the two halves in the flat text.
    start = text.index("12345678")
    z_index = text.index("Z", start)
    end = z_index + 1
    dni_substring = text[start:end]
    assert "\n" in dni_substring   # confirms the line break is inside the span

    out = tmp_path / "out.pdf"
    pdf_ops.redact_by_offsets(
        str(pdf), str(out),
        [_Span(start, end, dni_substring, "[DNI_1]")],
    )
    result_text = pdf_ops.extract_text(str(out))
    # Both digit body and letter must be gone.
    assert "12345678" not in result_text
    # The letter Z was on its own line so it should be gone or replaced.
    # (We can't strictly forbid the letter Z anywhere; check for the whole DNI.)
    assert "12345678Z" not in result_text.replace("\n", "").replace(" ", "")


def test_redact_multiple_spans_same_page(tmp_path: Path):
    pdf = _write_pdf(tmp_path, [
        "Juan Garcia y Maria Lopez firmaron.",
    ])
    text, _ = pdf_ops.extract_with_map(str(pdf))
    juan = text.index("Juan Garcia")
    maria = text.index("Maria Lopez")
    out = tmp_path / "out.pdf"
    pdf_ops.redact_by_offsets(
        str(pdf), str(out),
        [
            _Span(juan, juan + len("Juan Garcia"), "Juan Garcia", "[NOMBRE_1]"),
            _Span(maria, maria + len("Maria Lopez"), "Maria Lopez", "[NOMBRE_2]"),
        ],
    )
    result_text = pdf_ops.extract_text(str(out))
    assert "Juan Garcia" not in result_text
    assert "Maria Lopez" not in result_text


def test_redact_multiple_pages(tmp_path: Path):
    doc = fitz.open()
    for line in ["DNI 12345678Z uno", "DNI 00000000T dos"]:
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), line, fontsize=12, fontname="helv")
    pdf = tmp_path / "multi.pdf"
    doc.save(str(pdf))
    doc.close()

    text, _ = pdf_ops.extract_with_map(str(pdf))
    dni1 = text.index("12345678Z")
    dni2 = text.index("00000000T")
    out = tmp_path / "out.pdf"
    pdf_ops.redact_by_offsets(
        str(pdf), str(out),
        [
            _Span(dni1, dni1 + 9, "12345678Z", "[DNI_1]"),
            _Span(dni2, dni2 + 9, "00000000T", "[DNI_2]"),
        ],
    )
    result_text = pdf_ops.extract_text(str(out))
    assert "12345678Z" not in result_text
    assert "00000000T" not in result_text


def test_redact_empty_spans_is_noop(tmp_path: Path):
    pdf = _write_pdf(tmp_path, ["Content without PII."])
    out = tmp_path / "out.pdf"
    pdf_ops.redact_by_offsets(str(pdf), str(out), [])
    assert out.exists()
    assert "Content without PII." in pdf_ops.extract_text(str(out))


# --- Parallel page extraction ----------------------------------------------

@pytest.mark.parametrize("n, workers, expected", [
    (10, 4, [(0, 3), (3, 6), (6, 8), (8, 10)]),  # balanced, remainder up front
    (5, 5, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]),
    (2, 8, [(0, 1), (1, 2)]),                      # never more ranges than pages
    (1, 4, [(0, 1)]),
    (6, 3, [(0, 2), (2, 4), (4, 6)]),
])
def test_split_ranges(n, workers, expected):
    assert pdf_ops._split_ranges(n, workers) == expected


def test_split_ranges_covers_everything_without_overlap():
    ranges = pdf_ops._split_ranges(17, 4)
    covered = []
    for start, end in ranges:
        covered.extend(range(start, end))
    assert covered == list(range(17))


def _write_multipage(tmp_path: Path, pages: list[str]) -> Path:
    doc = fitz.open()
    for line in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), line, fontsize=12, fontname="helv")
    out = tmp_path / "multi.pdf"
    doc.save(str(out))
    doc.close()
    return out


def test_parallel_and_sequential_extraction_match(tmp_path: Path, monkeypatch):
    """A 4-page doc must extract identically whether run parallel or sequential."""
    pages = [
        "Pagina uno con DNI 12345678Z.",
        "Pagina dos con IBAN ES9121000418450200051332.",
        "Pagina tres con nombre Juan Garcia.",
        "Pagina cuatro final.",
    ]
    pdf = _write_multipage(tmp_path, pages)

    # Force sequential.
    monkeypatch.setattr("server.concurrency.worker_count", lambda: 1)
    seq = pdf_ops.extract_with_map(str(pdf))

    # Force parallel: 4 workers AND drop the OCR-page gate to 0 so text-layer
    # pages take the parallel branch (avoids needing scanned fixtures/Tesseract).
    monkeypatch.setattr("server.concurrency.worker_count", lambda: 4)
    monkeypatch.setattr(pdf_ops, "_MIN_OCR_PAGES_FOR_PARALLEL", 0)
    par = pdf_ops.extract_with_map(str(pdf))

    assert seq is not None and par is not None
    seq_text, seq_coords = seq
    par_text, par_coords = par
    assert seq_text == par_text
    assert len(seq_coords) == len(par_coords)
    # Rect coordinates must line up too (compare the non-None ones).
    for a, b in zip(seq_coords, par_coords):
        assert a.page_index == b.page_index
        if a.rect is None:
            assert b.rect is None
        else:
            assert abs(a.rect.x0 - b.rect.x0) < 0.01
            assert abs(a.rect.y0 - b.rect.y0) < 0.01


def test_parallel_extraction_offsets_still_redact(tmp_path: Path, monkeypatch):
    """After parallel extraction, offset-based redaction must still work."""
    pages = [f"Pagina {i} con DNI 12345678Z incluida." for i in range(4)]
    pdf = _write_multipage(tmp_path, pages)
    monkeypatch.setattr("server.concurrency.worker_count", lambda: 4)
    monkeypatch.setattr(pdf_ops, "_MIN_OCR_PAGES_FOR_PARALLEL", 0)

    text, _ = pdf_ops.extract_with_map(str(pdf))
    # Redact every occurrence of the DNI across all pages.
    spans = []
    idx = text.find("12345678Z")
    while idx != -1:
        spans.append(_Span(idx, idx + 9, "12345678Z", "[DNI_1]"))
        idx = text.find("12345678Z", idx + 1)
    assert len(spans) == 4

    out = tmp_path / "out.pdf"
    pdf_ops.redact_by_offsets(str(pdf), str(out), spans)
    assert "12345678Z" not in pdf_ops.extract_text(str(out))
