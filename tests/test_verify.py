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


def test_verify_pdf_never_triggers_ocr(monkeypatch, tmp_path: Path):
    """Regression: leak verification must not go through the OCR pipeline.

    Before this fix, verify called pdf_ops.extract_text which OCRs scanned
    pages, adding a third full OCR pass per document. Now it reads only the
    text layer directly, so ``pdf_ops.extract_text`` MUST NOT be called.
    """
    fitz = pytest.importorskip("fitz")
    from server import pdf_ops, verify

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "Hello world.", fontsize=12, fontname="helv")
    in_pdf = tmp_path / "in.pdf"
    doc.save(str(in_pdf))
    doc.close()

    called = {"n": 0}
    def boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("verify.find_leaks must not call pdf_ops.extract_text")
    monkeypatch.setattr(pdf_ops, "extract_text", boom)
    result = verify.find_leaks(str(in_pdf), ".pdf",
                                [_Span("Hello", "[G_1]")])
    # "Hello" is in the (unredacted) input, so it must be reported as leaked.
    assert "Hello" in result
    assert called["n"] == 0


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


# --- Blind verification on scanned output (safety) -------------------------


def test_can_verify_true_for_text_layer_pdf(tmp_path: Path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "Texto con capa real.", fontsize=12, fontname="helv")
    p = tmp_path / "text.pdf"
    doc.save(str(p)); doc.close()
    assert verify.can_verify(str(p), ".pdf") is True


def test_can_verify_false_for_image_only_pdf(tmp_path: Path):
    """A scanned output has no text layer, so a leak check sees nothing.

    Regression guard for the safety bug: find_leaks returned [] on scanned
    documents and the API reported 'leaked_pii_count: 0', which read as
    'verified clean' for a document that was never inspected. Measured A/B:
    156/156 un-redacted strings flagged on a text-layer PDF, 0/137 on the same
    document as a 200-dpi scan.
    """
    fitz = pytest.importorskip("fitz")
    Image = pytest.importorskip("PIL.Image")
    img = Image.new("L", (600, 800), 255)
    png = tmp_path / "blank.png"
    img.save(png)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(png))
    p = tmp_path / "scan.pdf"
    doc.save(str(p)); doc.close()
    assert verify.can_verify(str(p), ".pdf") is False


def test_can_verify_true_for_non_pdf():
    """DOCX/TXT always carry readable text, so the check is meaningful."""
    assert verify.can_verify("whatever.docx", ".docx") is True
    assert verify.can_verify("whatever.txt", ".txt") is True


def test_find_leaks_scans_each_distinct_string_once(tmp_path: Path):
    """Deduplication must not change the result, only the work."""
    p = tmp_path / "out.txt"
    p.write_text("Bob aparece. Bob otra vez. Bob de nuevo. Alice no fue tapada.")
    spans = [_Span("Bob", "[N_1]")] * 40 + [_Span("Alice", "[N_2]")]
    assert verify.find_leaks(str(p), ".txt", spans) == ["Alice", "Bob"]
