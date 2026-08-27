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


def test_extract_with_map_uses_cache(tmp_path: Path):
    """A second extract on the same file must skip work via the cache."""
    pdf = _write_pdf(tmp_path, ["Hello world"])
    pdf_ops.clear_extract_cache()
    first = pdf_ops.extract_with_map(str(pdf))
    # Monkey the underlying _extract_page function to a bomb; if the cache is
    # skipped, the second call would trip it. We do this via monkeypatch-style
    # attribute set/restore so the test doesn't need a pytest fixture here.
    import server.pdf_ops as mod
    original = mod._extract_page
    mod._extract_page = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("cache miss"))
    try:
        second = pdf_ops.extract_with_map(str(pdf))
    finally:
        mod._extract_page = original
        pdf_ops.clear_extract_cache()
    assert first is not None and second is not None
    assert first[0] == second[0]


def test_extract_cache_invalidated_on_mtime_change(tmp_path: Path):
    """Rewriting the file (same path, new mtime) must miss the cache."""
    import time
    pdf = _write_pdf(tmp_path, ["Uno"])
    pdf_ops.clear_extract_cache()
    a = pdf_ops.extract_with_map(str(pdf))
    time.sleep(0.05)  # coarse macOS mtime resolution
    _write_pdf(tmp_path, ["Dos"], filename="in.pdf")   # overwrites
    b = pdf_ops.extract_with_map(str(pdf))
    pdf_ops.clear_extract_cache()
    assert a is not None and b is not None
    assert "Uno" in a[0]
    assert "Dos" in b[0]


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


# --- OCR raster tuning -----------------------------------------------------


def test_effective_dpi_caps_at_native_resolution(tmp_path: Path, monkeypatch):
    """Never upsample: a 150-dpi scan must not be rendered at 200 dpi.

    Rasterising above the embedded image's own resolution costs pixels and
    interpolates no detail, and measurably regressed identifier recall on
    low-resolution sources.
    """
    Image = pytest.importorskip("PIL.Image")
    # A 595x842 pt page carrying a ~150 dpi image (1240x1754 px).
    img = Image.new("L", (1240, 1754), 255)
    png = tmp_path / "scan.png"
    img.save(png)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(png))
    p = tmp_path / "scan.pdf"
    doc.save(str(p)); doc.close()

    monkeypatch.setenv("PF_OCR_DPI", "200")
    monkeypatch.delenv("PF_OCR_CLAMP_NATIVE", raising=False)
    reopened = fitz.open(str(p))
    try:
        native = pdf_ops._native_dpi(reopened[0])
        eff = pdf_ops._effective_dpi(reopened[0])
    finally:
        reopened.close()
    assert 140 <= native <= 160, native
    assert eff <= 200 and eff <= native + 1, (eff, native)


def test_effective_dpi_uses_ceiling_when_page_has_no_image(tmp_path: Path, monkeypatch):
    pdf = _write_pdf(tmp_path, ["Solo texto, sin imagen."])
    monkeypatch.setenv("PF_OCR_DPI", "200")
    monkeypatch.delenv("PF_OCR_CLAMP_NATIVE", raising=False)
    doc = fitz.open(str(pdf))
    try:
        assert pdf_ops._native_dpi(doc[0]) == 0.0
        assert pdf_ops._effective_dpi(doc[0]) == 200
    finally:
        doc.close()


def test_effective_dpi_never_below_floor(tmp_path: Path, monkeypatch):
    """A very coarse scan is still rendered at the floor, not lower."""
    Image = pytest.importorskip("PIL.Image")
    img = Image.new("L", (300, 420), 255)          # ~36 dpi on an A4 page
    png = tmp_path / "coarse.png"
    img.save(png)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(png))
    p = tmp_path / "coarse.pdf"
    doc.save(str(p)); doc.close()
    monkeypatch.setenv("PF_OCR_DPI", "200")
    reopened = fitz.open(str(p))
    try:
        assert pdf_ops._effective_dpi(reopened[0]) == pdf_ops._MIN_OCR_DPI
    finally:
        reopened.close()


def test_clamp_can_be_disabled(tmp_path: Path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    img = Image.new("L", (1240, 1754), 255)
    png = tmp_path / "s.png"; img.save(png)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(png))
    p = tmp_path / "s.pdf"; doc.save(str(p)); doc.close()
    monkeypatch.setenv("PF_OCR_DPI", "300")
    monkeypatch.setenv("PF_OCR_CLAMP_NATIVE", "0")
    reopened = fitz.open(str(p))
    try:
        assert pdf_ops._effective_dpi(reopened[0]) == 300
    finally:
        reopened.close()


# --- Output size -----------------------------------------------------------


def test_redacted_output_is_compressed(tmp_path: Path):
    """apply_redactions re-encodes pages; without deflate the output exploded.

    Measured on a 24-page scan: 8.3 MB in -> 286.9 MB out (~12 MB/page, i.e.
    ~2.8 GB for a 232-page file). This asserts the saved file stays in the same
    order of magnitude as its input rather than ballooning.
    """
    pdf = _write_pdf(tmp_path, ["Nombre: Juan Garcia Perez, DNI 12345678Z"])
    text, _ = pdf_ops.extract_with_map(str(pdf))
    start = text.index("Juan Garcia Perez")
    out = tmp_path / "out.pdf"
    pdf_ops.redact_by_offsets(
        str(pdf), str(out),
        [_Span(start, start + 17, "Juan Garcia Perez", "[NOMBRE_1]")],
    )
    assert out.stat().st_size < pdf.stat().st_size * 8


# --- Extraction cache keyed on content, not path ---------------------------


def test_cache_hits_for_identical_content_at_a_different_path(tmp_path: Path):
    """The regenerate endpoint re-uploads the same bytes to a new temp path.

    With a path-keyed cache that always missed and the whole document was OCR'd
    a second time (~257 s on a 232-page scan).
    """
    import shutil
    pdf = _write_pdf(tmp_path, ["Contenido identico"], filename="a.pdf")
    copy = tmp_path / "b.pdf"
    shutil.copyfile(pdf, copy)
    pdf_ops.clear_extract_cache()
    first = pdf_ops.extract_with_map(str(pdf))
    import server.pdf_ops as mod
    original = mod._extract_page
    mod._extract_page = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("cache miss on identical content"))
    try:
        second = pdf_ops.extract_with_map(str(copy))
    finally:
        mod._extract_page = original
        pdf_ops.clear_extract_cache()
    assert first is not None and second is not None
    assert first[0] == second[0]


def test_cache_misses_for_different_content(tmp_path: Path):
    pdf_ops.clear_extract_cache()
    a = _write_pdf(tmp_path, ["Uno"], filename="x.pdf")
    ra = pdf_ops.extract_with_map(str(a))
    b = _write_pdf(tmp_path, ["Dos"], filename="y.pdf")
    rb = pdf_ops.extract_with_map(str(b))
    pdf_ops.clear_extract_cache()
    assert "Uno" in ra[0] and "Dos" in rb[0]


# --- OCR line structure ----------------------------------------------------


class _FakeTesseract:
    """Stand-in for ``pytesseract`` returning a fixed ``image_to_data`` dict.

    Lets the OCR assembly be tested without the tesseract binary, which CI
    runners do not have.
    """

    class Output:
        DICT = "dict"

    class TesseractNotFoundError(Exception):
        pass

    def __init__(self, data: dict):
        self._data = data

    def image_to_data(self, image, lang=None, output_type=None):
        return self._data


def _fake_ocr_data(words: list[tuple[str, int, int, int]]) -> dict:
    """Build an ``image_to_data`` dict from ``(text, block, par, line)`` tuples."""
    data = {
        "text": [], "block_num": [], "par_num": [], "line_num": [],
        "left": [], "top": [], "width": [], "height": [],
    }
    for index, (word, block, par, line) in enumerate(words):
        data["text"].append(word)
        data["block_num"].append(block)
        data["par_num"].append(par)
        data["line_num"].append(line)
        data["left"].append(10 + index * 50)
        data["top"].append(20 + line * 30)
        data["width"].append(len(word) * 8)
        data["height"].append(14)
    return data


def _ocr_a_blank_page(monkeypatch, tmp_path: Path, data: dict):
    """Run ``_ocr_page`` over a blank page with ``pytesseract`` stubbed out."""
    monkeypatch.setitem(sys.modules, "pytesseract", _FakeTesseract(data))
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    try:
        return pdf_ops._ocr_page(page, 0, fitz)
    finally:
        doc.close()


def test_ocr_page_separates_lines_and_paragraphs(monkeypatch, tmp_path: Path):
    """OCR output must carry the page's line and block structure.

    The text-layer path in :func:`server.pdf_ops._extract_page` emits a newline
    per line and a second one per block. The OCR path has to match, because the
    analyzer runs NER per line as well as per paragraph
    (:func:`server.ner_es._iter_segments`): a page delivered as one long line
    silently loses that pass, and names sitting on their own header line are the
    exact case it exists to catch.
    """
    data = _fake_ocr_data([
        ("Procedimiento", 1, 1, 1), ("282/2010", 1, 1, 1),
        ("MENOR:", 1, 1, 2), ("JOSEPH", 1, 1, 2), ("PANTA", 1, 1, 2),
        ("Vistas", 2, 1, 1), ("las", 2, 1, 1), ("diligencias", 2, 1, 1),
    ])
    text, coords = _ocr_a_blank_page(monkeypatch, tmp_path, data)

    assert text == (
        "Procedimiento 282/2010\n"
        "MENOR: JOSEPH PANTA\n"
        "\n"
        "Vistas las diligencias\n"
    )
    assert len(text) == len(coords)


def test_ocr_page_separators_have_no_rectangle(monkeypatch, tmp_path: Path):
    """Synthetic separators must not be redactable: no glyph, so no rect."""
    data = _fake_ocr_data([
        ("Nombre", 1, 1, 1),
        ("Juan", 1, 1, 2),
        ("Otro", 2, 1, 1),
    ])
    text, coords = _ocr_a_blank_page(monkeypatch, tmp_path, data)

    for index, char in enumerate(text):
        if char.isspace():
            assert coords[index].rect is None, f"separator at {index} has a rect"
        else:
            assert coords[index].rect is not None, f"glyph at {index} has no rect"
        assert coords[index].from_ocr is True


@pytest.mark.skipif(
    __import__("shutil").which("tesseract") is None,
    reason="tesseract binary not installed",
)
def test_real_ocr_of_a_scanned_page_keeps_one_line_per_line(tmp_path: Path):
    """End-to-end: rasterise a text PDF, OCR it back, expect the same lines."""
    pytest.importorskip("pytesseract")
    pytest.importorskip("PIL.Image")
    lines = [
        "Procedimiento Expediente 282/2010",
        "MENOR: JOSEPH KEVIN PANTA MINAYA",
    ]
    source = _write_pdf(tmp_path, lines, filename="text-layer.pdf")
    doc = fitz.open(str(source))
    pixmap = doc[0].get_pixmap(dpi=200)
    scan = fitz.open()
    page = scan.new_page(width=doc[0].rect.width, height=doc[0].rect.height)
    page.insert_image(page.rect, pixmap=pixmap)
    out = tmp_path / "scan.pdf"
    scan.save(str(out)); scan.close(); doc.close()

    pdf_ops.clear_extract_cache()
    result = pdf_ops.extract_with_map(str(out))
    assert result is not None
    text, coords = result
    assert len(text) == len(coords)
    assert "MENOR: JOSEPH KEVIN PANTA MINAYA" in text
    # The two source lines must not have been fused into one.
    assert "282/2010\nMENOR:" in text
