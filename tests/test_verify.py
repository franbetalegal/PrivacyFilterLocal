"""Tests for the post-redaction anti-leak verification."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import verify


class _Span:
    def __init__(self, text: str, placeholder: str):
        self.text = text
        self.placeholder = placeholder


def test_find_leaks_on_plain_text_file(tmp_path: Path):
    p = tmp_path / "out.txt"
    p.write_text("El interesado, con [DNI_1], firma. Sobra Juan Garcia.")
    spans = [
        _Span("Juan Garcia", "[NOMBRE_1]"),   # still present → leaked
        _Span("12345678Z", "[DNI_1]"),        # replaced → OK
    ]
    leaked = verify.find_leaks(str(p), ".txt", spans)
    assert leaked == ["Juan Garcia"]


def test_find_leaks_ignores_spans_whose_text_equals_placeholder(tmp_path: Path):
    p = tmp_path / "out.txt"
    p.write_text("[NOMBRE_1] compareció.")
    spans = [_Span("[NOMBRE_1]", "[NOMBRE_1]")]
    assert verify.find_leaks(str(p), ".txt", spans) == []


def test_find_leaks_empty_output(tmp_path: Path):
    p = tmp_path / "empty.txt"
    p.write_text("")
    assert verify.find_leaks(str(p), ".txt", [_Span("X", "[Y]")]) == []


def test_find_leaks_deduplicates_and_sorts(tmp_path: Path):
    p = tmp_path / "out.txt"
    p.write_text("Bob y Alice; también Bob otra vez.")
    spans = [
        _Span("Bob", "[N_1]"),
        _Span("Bob", "[N_1]"),
        _Span("Alice", "[N_2]"),
    ]
    assert verify.find_leaks(str(p), ".txt", spans) == ["Alice", "Bob"]


def test_find_leaks_pdf_roundtrip(tmp_path: Path):
    fitz = pytest.importorskip("fitz")
    from server import pdf_ops

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "El DNI 12345678Z aparece aquí.",
                     fontsize=12, fontname="helv")
    in_pdf = tmp_path / "in.pdf"
    doc.save(str(in_pdf))
    doc.close()

    # First: no redaction → the string leaks.
    leaked = verify.find_leaks(str(in_pdf), ".pdf",
                               [_Span("12345678Z", "[DNI_1]")])
    assert leaked == ["12345678Z"]

    # Now redact and re-verify → no leaks.
    text, _ = pdf_ops.extract_with_map(str(in_pdf))
    start = text.index("12345678Z")
    class S: pass
    s = S(); s.start = start; s.end = start + 9
    s.text = "12345678Z"; s.placeholder = "[DNI_1]"
    out_pdf = tmp_path / "out.pdf"
    pdf_ops.redact_by_offsets(str(in_pdf), str(out_pdf), [s])
    assert verify.find_leaks(str(out_pdf), ".pdf", [s]) == []
