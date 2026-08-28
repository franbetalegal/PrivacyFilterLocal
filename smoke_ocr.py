"""Release smoke test: prove a built package can read a scanned page.

Runs on every platform we publish. It renders a page of known text to an image
with no text layer, puts it through the real extraction path in
``server.pdf_ops``, and asserts the words come back.

Why this exists: without Tesseract, a scanned PDF extracts as an empty string,
the pipeline finds nothing, and the interface reports "no detections" — which
reads exactly like a document that has no personal data in it. That is the
worst possible failure for an anonymiser, and until 2.7.0 it was reported only
in the log. Bundling Tesseract fixes it; this is what keeps it fixed.

    python smoke_ocr.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Invented, and deliberately in the shape the analyzer cares about: a name in
# caps on a line of its own. The OCR only has to return the words; whether they
# are then redacted is smoke_ner.py's question.
LINES = ["EXPEDIENTE DE PRUEBA", "AURORA VIDALBA SERENA", "Calle Inventada 42"]


def _render_page_without_text_layer(path: Path) -> None:
    """Write a one-page PDF whose only content is a picture of the text.

    Drawing the text and then rasterising the page is what makes this a fair
    test: the result has no text layer at all, so extraction has to fall back
    to OCR exactly as it does on a real scan.
    """
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    y = 120
    for line in LINES:
        page.insert_text((72, y), line, fontsize=22, fontname="helv")
        y += 44
    pixmap = page.get_pixmap(dpi=300)
    doc.close()

    scanned = fitz.open()
    scanned_page = scanned.new_page(width=pixmap.width * 72 / 300,
                                    height=pixmap.height * 72 / 300)
    scanned_page.insert_image(scanned_page.rect, pixmap=pixmap)
    scanned.save(str(path))
    scanned.close()


def main() -> int:
    from server import inference, pdf_ops

    ocr = inference.ocr_status()
    print(f"Tesseract: available={ocr['available']} binary={ocr['binary']}")
    if not ocr["available"]:
        print(
            "FAIL: this build has no Tesseract. Scanned PDFs would extract as "
            "empty text and the app would report no detections in them."
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="pf_ocr_smoke_") as tmp:
        pdf = Path(tmp) / "scanned.pdf"
        _render_page_without_text_layer(pdf)

        extracted = pdf_ops.extract_with_map(str(pdf))
        if extracted is None:
            print("FAIL: the PDF could not be extracted at all.")
            return 1
        text, coords = extracted

    from_ocr = sum(1 for c in coords if c.from_ocr)
    print(f"Extracted {len(text)} chars, {from_ocr} of them from OCR")
    if from_ocr == 0:
        print(
            "FAIL: nothing came from OCR. The page was rendered without a text "
            "layer, so the OCR path should have been the one that ran."
        )
        return 1

    upper = text.upper()
    missing = [w for w in ("AURORA", "VIDALBA", "SERENA") if w not in upper]
    if missing:
        print(f"Extracted text: {text!r}")
        print(f"FAIL: OCR ran but did not return {missing}.")
        return 1

    # Per-word rectangles are what keep redaction from covering whole lines.
    boxed = sum(1 for c in coords if c.rect is not None)
    print(f"Characters with a rectangle: {boxed}")
    if boxed == 0:
        print("FAIL: OCR returned text with no coordinates; redaction needs them.")
        return 1

    print("OK: the packaged environment reads a page that has no text layer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
