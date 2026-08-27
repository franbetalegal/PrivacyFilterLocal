"""Ad-hoc diagnostic: why a given piece of text in a PDF was not redacted.

Not part of the server. Two modes, both run locally on the document that
failed; nothing leaves the machine either way.

Trace one span:

    source .venv/bin/activate
    python diagnose_detection.py path/to/document.pdf "TEXT THAT WAS MISSED"

Reports, for the extracted line containing the text: whether the page came from
the embedded text layer or from OCR, how the line was extracted, whether spaCy
NER proposes the span, whether the false-positive filter rejects it, and what
the full pipeline finally redacts.

Compare the two OCR assemblies:

    python diagnose_detection.py --compare path/to/document.pdf

Runs the whole document both ways — one text line per page line (the default)
and the whole page joined by spaces (``PF_OCR_LINE_BREAKS=0``) — and prints how
many entities each finds, per label. Output is counts and label names only, no
document text, so it is safe to share when the document is not.
"""
import os
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


def compare(pdf_path: str) -> int:
    """Count entities found with and without OCR line breaks. Counts only."""
    from server import inference, pipeline

    model = inference.get_model()
    results = {}
    for mode, flag in (("spaces", "0"), ("line breaks (default)", "1")):
        os.environ["PF_OCR_LINE_BREAKS"] = flag
        # Extraction workers are separate processes that inherit the
        # environment at spawn time, so a pool left over from the previous mode
        # would still be running with the previous flag and both modes would
        # report identical numbers.
        pdf_ops._shutdown_pdf_pool()
        pdf_ops.clear_extract_cache()
        extracted = pdf_ops.extract_with_map(pdf_path)
        if extracted is None:
            print("Could not extract the PDF.")
            return 1
        text, coords = extracted
        redaction = pipeline.redact(model, text)
        by_label: dict[str, int] = {}
        for span in redaction.detected_spans:
            by_label[span.label] = by_label.get(span.label, 0) + 1
        results[mode] = {
            "characters": len(text),
            "from_ocr": sum(1 for c in coords if c.from_ocr),
            "lines": text.count("\n"),
            "blank_lines": text.count("\n\n"),
            "entities": len(redaction.detected_spans),
            "by_label": by_label,
        }

    for mode, stats in results.items():
        print(f"\n-- {mode} --")
        print(f"  characters: {stats['characters']}  from OCR: {stats['from_ocr']}")
        print(f"  lines: {stats['lines']}  blank lines: {stats['blank_lines']}")
        print(f"  entities: {stats['entities']}")
        for label, count in sorted(stats["by_label"].items()):
            print(f"    {label}: {count}")

    counts = [stats["entities"] for stats in results.values()]
    print(f"\nspaces {counts[0]} vs line breaks {counts[1]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--compare":
        raise SystemExit(compare(sys.argv[2]))
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
