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


def _report_con_fuga() -> EvalReport:
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


def test_informe_seguro_cuenta_las_fugas_sin_revelar_el_texto():
    seguro = baseline_cli._informe_seguro(_report_con_fuga())

    assert seguro["leak_count"] == 1
    serializado = json.dumps(seguro, ensure_ascii=False)
    assert "Melitona" not in serializado
    assert "Zarrabeitia" not in serializado
    assert "leaks" not in seguro


def test_informe_seguro_conserva_las_metricas():
    seguro = baseline_cli._informe_seguro(_report_con_fuga())

    assert seguro["mode"] == "balanced"
    assert seguro["examples"] == 3
    assert seguro["overall"]["f1"] == 0.5
    assert seguro["exact"]["f1"] == 0.25
    assert seguro["by_label"]["NOMBRE"]["gold"] == 2


def test_tabla_no_incluye_texto_de_ejemplo_ni_de_fuga():
    tabla = baseline_cli._render_tabla([_informe := baseline_cli._informe_seguro(_report_con_fuga())])

    assert "Melitona" not in tabla
    assert "NOMBRE" in tabla
    assert "balanced" in tabla


def test_soporte_bajo_se_marca_como_no_concluyente():
    report = EvalReport(
        overall={"precision": 1.0, "recall": 1.0, "f1": 1.0},
        exact={"precision": 1.0, "recall": 1.0, "f1": 1.0},
        by_label={
            "ESCASO": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "pred": 2, "gold": 2},
            "SUFICIENTE": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "pred": 40, "gold": 40},
        },
        mode="balanced",
    )
    seguro = baseline_cli._informe_seguro(report)

    assert seguro["by_label"]["ESCASO"]["poco_soporte"] is True
    assert seguro["by_label"]["SUFICIENTE"]["poco_soporte"] is False


def test_carga_el_corpus_sintetico_del_repositorio():
    ejemplos = baseline_cli.cargar_ejemplos(baseline_cli.CORPUS_SINTETICO)

    # Deliberately not an exact count: the corpus grows as new failure patterns
    # are found, and a hardcoded number turns every addition into a test edit.
    assert len(ejemplos) >= 30
    assert all(isinstance(ex, Example) for ex in ejemplos)
    con_spans = [ex for ex in ejemplos if ex.spans]
    assert con_spans, "the corpus must carry annotated spans"
    for ex in con_spans:
        for span in ex.spans:
            assert isinstance(span, GoldSpan)
            # Offsets must actually address the text they claim to.
            assert ex.text[span.start : span.end].strip()
