"""Ad-hoc diagnostic: why a given piece of text in a PDF was not redacted.

Not part of the server. Run it locally on the document that failed:

    source .venv/bin/activate
    python diagnose_detection.py path/to/document.pdf "TEXT THAT WAS MISSED"

It reports, for the extracted line containing the text: whether the page came
from the embedded text layer or from OCR, how the line was extracted, whether
spaCy NER proposes the span, whether the false-positive filter rejects it, and
what the full pipeline finally redacts. Nothing leaves the machine.
"""
import sys

from server import ner_es, pdf_ops


def main(pdf_path: str, needle: str) -> int:
    result = pdf_ops.extract_with_map(pdf_path)
    if result is None:
        print("Could not extract the PDF.")
        return 1
    text, coords = result
    print(f"Extracted characters: {len(text)}")
    from_ocr = sum(1 for c in coords if c.from_ocr)
    print(f"Coming from OCR: {from_ocr} ({from_ocr * 100 // max(1, len(coords))}%)")

    index = text.find(needle)
    if index < 0:
        print(f"\n{needle!r} does NOT appear in the extracted text.")
        print("Detection cannot be blamed: the text never reached the analyzer.")
        print("Likely cause: degraded OCR or text layer. Per-word occurrences:")
        for word in needle.split():
            print(f"  {word!r}: {text.count(word)}")
        return 0

    line_start = text.rfind("\n", 0, index) + 1
    line_end = text.find("\n", index)
    line = text[line_start:line_end if line_end >= 0 else len(text)]
    print(f"\nFound at offset {index}.")
    print(f"Extracted line: {line!r}")

    print("\n-- NER on the line --")
    for span in ner_es.analyze(line):
        print(f"  {span.entity_type} {span.text!r}")

    print("\n-- NER on the whole document --")
    hits = [s for s in ner_es.analyze(text) if needle in s.text or s.text in needle]
    for span in hits:
        print(f"  {span.entity_type} {span.text!r} @ {span.start}")
    if not hits:
        print("  no match: the model does not propose the entity")

    print("\n-- False-positive filter --")
    context = text[max(0, index - 120):index]
    rejected = ner_es.is_probably_false_positive(
        needle, strict=True, label="ES_NER_PER", context=context
    )
    print(f"  rejected by the filter: {rejected}")

    print("\n-- Full pipeline --")
    from server import inference, pipeline
    redaction = pipeline.redact(inference.get_model(), text)
    for span in redaction.detected_spans:
        if needle in span.text or span.text in needle:
            print(f"  redacted as {span.label}: {span.text!r}")
            break
    else:
        print(f"  {needle!r} is NOT redacted")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
