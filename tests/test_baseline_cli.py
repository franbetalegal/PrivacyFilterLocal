"""Tests for the detection baseline runner.

The load-bearing test here is the privacy one: ``EvalReport.leaks`` carries the
original PII text (``evaluation.Leak.text``), and ``EvalReport.as_dict()``
serialises it verbatim. The runner must never emit that, because the same
command will eventually be pointed at a gold store built from real documents.
"""

from __future__ import annotations

import json

from server import baseline_cli
from server.dataset import Example, GoldSpan
from server.evaluation import EvalReport, Leak


def _report_with_leak() -> EvalReport:
    return EvalReport(
        overall={"precision": 0.5, "recall": 0.5, "f1": 0.5},
        exact={"precision": 0.25, "recall": 0.25, "f1": 0.25},
        by_label={"NOMBRE": {"precision": 1.0, "recall": 0.5, "f1": 0.67, "pred": 1, "gold": 2}},
        leaks=[Leak(example_index=0, text="Melitona Zarrabeitia", label="NOMBRE")],
        examples=3,
        gold_spans=2,
        pred_spans=1,
        mode="balanced",
    )


def test_safe_report_counts_leaks_without_revealing_text():
    safe = baseline_cli._safe_report(_report_with_leak())

    assert safe["leak_count"] == 1
    serialised = json.dumps(safe, ensure_ascii=False)
    assert "Melitona" not in serialised
    assert "Zarrabeitia" not in serialised
    assert "leaks" not in safe


def test_safe_report_keeps_the_metrics():
    safe = baseline_cli._safe_report(_report_with_leak())

    assert safe["mode"] == "balanced"
    assert safe["examples"] == 3
    assert safe["overall"]["f1"] == 0.5
    assert safe["exact"]["f1"] == 0.25
    assert safe["by_label"]["NOMBRE"]["gold"] == 2


def test_table_excludes_example_and_leak_text():
    table = baseline_cli._render_table([_informe := baseline_cli._safe_report(_report_with_leak())])

    assert "Melitona" not in table
    assert "NOMBRE" in table
    assert "balanced" in table


def test_low_support_is_flagged_as_inconclusive():
    report = EvalReport(
        overall={"precision": 1.0, "recall": 1.0, "f1": 1.0},
        exact={"precision": 1.0, "recall": 1.0, "f1": 1.0},
        by_label={
            "ESCASO": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "pred": 2, "gold": 2},
            "SUFICIENTE": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "pred": 40, "gold": 40},
        },
        mode="balanced",
    )
    safe = baseline_cli._safe_report(report)

    assert safe["by_label"]["ESCASO"]["low_support"] is True
    assert safe["by_label"]["SUFICIENTE"]["low_support"] is False


def test_loads_the_repository_synthetic_corpus():
    examples = baseline_cli.load_examples(baseline_cli.SYNTHETIC_CORPUS)

    # Deliberately not an exact count: the corpus grows as new failure patterns
    # are found, and a hardcoded number turns every addition into a test edit.
    assert len(examples) >= 30
    assert all(isinstance(ex, Example) for ex in examples)
    with_spans = [ex for ex in examples if ex.spans]
    assert with_spans, "the corpus must carry annotated spans"
    for ex in with_spans:
        for span in ex.spans:
            assert isinstance(span, GoldSpan)
            # Offsets must actually address the text they claim to.
            assert ex.text[span.start : span.end].strip()
