"""Tests for the Spanish NER layer.

The full test module is skipped when spaCy or its Spanish model isn't
installed, so CI can pass without the ~560 MB download; the pipeline layer
tests separately assert that missing spaCy degrades to an empty NER list.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import ner_es

if not ner_es.is_available():
    pytest.skip(
        "Spanish spaCy model not installed. "
        "Install with 'pip install spacy && python -m spacy download es_core_news_lg'.",
        allow_module_level=True,
    )


def test_analyze_detects_person_and_location():
    text = "Juan García Pérez comparece ante el Juzgado de Madrid."
    labels = {s.entity_type for s in ner_es.analyze(text)}
    assert "ES_NER_PER" in labels
    assert "ES_NER_LOC" in labels


def test_analyze_offsets_map_back_to_original_text():
    text = "El interesado, Juan García, vive en Madrid."
    for span in ner_es.analyze(text):
        assert text[span.start:span.end] == span.text


def test_analyze_empty_text():
    assert ner_es.analyze("") == []


def test_analyze_score_below_opf_and_above_low_deterministic():
    text = "Juan García firmó."
    spans = ner_es.analyze(text)
    assert spans, "Expected at least one span for a clear PER"
    for s in spans:
        # opf default weight is 0.85; deterministic postal code is 0.45.
        assert 0.5 < s.score < 0.85
