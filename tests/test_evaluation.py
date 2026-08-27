"""Tests for the pipeline evaluator + leak gate (server.evaluation).

These run model-free: the injected ``redact_fn`` drives the real pipeline with
an empty opf result, so only the deterministic recognizers and the custom
dictionary fire. That is enough to exercise every metric path and the leak
check without needing a checkpoint in CI. spaCy NER is stubbed to empty so the
outcome is deterministic across machines.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import evaluation
from server.dataset import Example, GoldSpan

pytest.importorskip("opf")
from opf._api import RedactionResult


@pytest.fixture
def redact_fn(monkeypatch, tmp_path):
    """A model-free pipeline: deterministic + dictionary only."""
    from server import pipeline, custom_dict
    from server.custom_dict import Store

    # Isolate NER (empty) and the dictionary (temp store) for reproducibility.
    monkeypatch.setattr(pipeline.ner_es, "analyze", lambda text, *, strict: [])
    store = Store(path=tmp_path / "d.json")
    monkeypatch.setattr(custom_dict, "get_store", lambda: store)

    def fn(text: str, mode: str):
        stub = RedactionResult(
            schema_version="1", summary={}, text=text,
            detected_spans=(), redacted_text=text, warning=None,
        )
        return pipeline.merge_and_redact(text, stub, mode=mode)

    fn.store = store  # expose so a test can seed dictionary terms
    return fn


# 12345678Z is a structurally valid DNI (mod-23 → 'Z').
DNI_TEXT = "El interesado con DNI 12345678Z comparece."
DNI_START = DNI_TEXT.index("12345678Z")


def test_perfect_detection_scores_one(redact_fn):
    ex = Example(DNI_TEXT, (GoldSpan(DNI_START, DNI_START + 9, "DNI"),))
    report = evaluation.evaluate([ex], redact_fn)
    assert report.overall["recall"] == 1.0
    assert report.overall["precision"] == 1.0
    assert report.overall["f1"] == 1.0
    assert report.leaks == []
    assert report.by_label["DNI"]["recall"] == 1.0


def test_missed_gold_span_lowers_recall_and_leaks(redact_fn):
    # Gold marks a name the model-free pipeline cannot detect → miss + leak.
    text = "Compareció Anselmo del Valle."
    start = text.index("Anselmo del Valle")
    ex = Example(text, (GoldSpan(start, start + len("Anselmo del Valle"), "NOMBRE"),))
    report = evaluation.evaluate([ex], redact_fn)
    assert report.overall["recall"] == 0.0
    assert report.overall["fn"] == 1
    # The undetected name survives verbatim in the output → recorded as a leak.
    assert len(report.leaks) == 1
    assert report.leaks[0].text == "Anselmo del Valle"
    assert report.leaks[0].label == "NOMBRE"


def test_false_positive_lowers_precision(redact_fn):
    # A DNI is present and detected, but the gold set does NOT label it, so the
    # correct detection counts as a false positive against this (wrong) gold.
    ex = Example(DNI_TEXT, ())  # no gold spans
    report = evaluation.evaluate([ex], redact_fn)
    assert report.overall["fp"] == 1
    assert report.overall["precision"] == 0.0


def test_dictionary_term_is_measured(redact_fn):
    redact_fn.store.add("OMEGA PROPERTY SL", label="EMPRESA")
    text = "La mercantil OMEGA PROPERTY SL comparece."
    start = text.index("OMEGA PROPERTY SL")
    ex = Example(text, (GoldSpan(start, start + 17, "EMPRESA"),))
    report = evaluation.evaluate([ex], redact_fn)
    assert report.by_label["EMPRESA"]["recall"] == 1.0
    assert report.leaks == []


def test_relaxed_vs_exact_matching(redact_fn):
    # Gold boundary is one char shorter than the detected DNI span. Relaxed
    # (overlap) matching counts it found; exact matching counts it missed.
    ex = Example(DNI_TEXT, (GoldSpan(DNI_START, DNI_START + 8, "DNI"),))
    report = evaluation.evaluate([ex], redact_fn)
    assert report.overall["recall"] == 1.0     # overlap → found
    assert report.exact["recall"] == 0.0       # boundaries differ → miss


def test_report_serializes(redact_fn):
    ex = Example(DNI_TEXT, (GoldSpan(DNI_START, DNI_START + 9, "DNI"),))
    d = evaluation.evaluate([ex], redact_fn).as_dict()
    assert d["examples"] == 1
    assert d["leak_count"] == 0
    assert "overall" in d and "by_label" in d and "exact" in d
