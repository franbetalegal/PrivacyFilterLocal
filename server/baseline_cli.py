"""Measure detection quality against a labelled dataset.

``server/evaluation.py`` knows how to score the pipeline but nothing wires it to
a dataset, so there is no way to tell whether a change to the false-positive
filter helped. This module is that wiring: it loads a labelled corpus, runs the
real redaction pipeline over it in each operating mode, and prints per-label
precision / recall / F1.

Usage::

    .venv/bin/python -m server.baseline_cli                    # synthetic corpus
    .venv/bin/python -m server.baseline_cli --dataset data/gold.jsonl
    .venv/bin/python -m server.baseline_cli --json informe.json

The default dataset is the synthetic corpus committed under ``tests/fixtures``,
which contains no real personal data. Pointing ``--dataset`` at a gold store
built from real documents is supported and safe: the report deliberately omits
``EvalReport.leaks``, whose ``text`` field holds the original PII verbatim, and
reports only how many leaks were found. Do not add example text to the output.

Interpreting the table: a label whose gold count is below
``SOPORTE_MINIMO`` is marked ``poco soporte``. Its F1 is arithmetic, not
evidence — do not tune anything on the strength of a handful of spans.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from server import PROJECT_DIR
from server.dataset import Example, GoldStore
from server.evaluation import EvalReport, score_examples

CORPUS_SINTETICO = PROJECT_DIR / "tests" / "fixtures" / "gold_sintetico.jsonl"

MODOS = ("conservative", "balanced", "aggressive")

# Below this many gold spans a per-label metric is noise, not signal.
SOPORTE_MINIMO = 10


def cargar_ejemplos(path: Path) -> list[Example]:
    """Load labelled examples from a JSONL gold store."""
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")
    return GoldStore(path).load_examples()


def _informe_seguro(report: EvalReport) -> dict:
    """Project an EvalReport onto the fields that are safe to print.

    Everything derived from document text is dropped. ``EvalReport.leaks``
    entries carry the original PII in ``Leak.text``; only the count survives.
    """
    by_label: dict[str, dict] = {}
    for label, metricas in report.by_label.items():
        gold = int(metricas.get("gold", 0))
        by_label[label] = {
            "precision": metricas.get("precision"),
            "recall": metricas.get("recall"),
            "f1": metricas.get("f1"),
            "pred": metricas.get("pred"),
            "gold": gold,
            "poco_soporte": gold < SOPORTE_MINIMO,
        }
    return {
        "mode": report.mode,
        "examples": report.examples,
        "gold_spans": report.gold_spans,
        "pred_spans": report.pred_spans,
        "overall": dict(report.overall),
        "exact": dict(report.exact),
        "by_label": by_label,
        "leak_count": len(report.leaks),
    }


async def _redactar_todo(ejemplos: list[Example], modo: str) -> list:
    from server import inference

    resultados = []
    for ejemplo in ejemplos:
        resultados.append(await inference.redact(ejemplo.text, modo))
    return resultados


def evaluar_modo(ejemplos: list[Example], modo: str) -> dict:
    """Run the pipeline over the corpus in one mode and return a safe report."""
    resultados = asyncio.run(_redactar_todo(ejemplos, modo))
    return _informe_seguro(score_examples(ejemplos, resultados, mode=modo))


def _fmt(valor) -> str:
    return f"{valor:.3f}" if isinstance(valor, (int, float)) else "-"


def _render_tabla(informes: list[dict]) -> str:
    lineas: list[str] = []
    for informe in informes:
        lineas.append("")
        lineas.append(
            f"modo {informe['mode']}  "
            f"ejemplos={informe['examples']}  "
            f"gold={informe['gold_spans']}  "
            f"pred={informe['pred_spans']}  "
            f"fugas={informe['leak_count']}"
        )
        glob = informe["overall"]
        exact = informe["exact"]
        lineas.append(
            f"  global (solapamiento)  P={_fmt(glob.get('precision'))} "
            f"R={_fmt(glob.get('recall'))} F1={_fmt(glob.get('f1'))}"
        )
        lineas.append(
            f"  global (exacto)        P={_fmt(exact.get('precision'))} "
            f"R={_fmt(exact.get('recall'))} F1={_fmt(exact.get('f1'))}"
        )
        if informe["by_label"]:
            lineas.append(f"  {'etiqueta':<16}{'P':>7}{'R':>7}{'F1':>7}{'pred':>7}{'gold':>7}")
            for label in sorted(informe["by_label"]):
                m = informe["by_label"][label]
                aviso = "  poco soporte" if m["poco_soporte"] else ""
                lineas.append(
                    f"  {label:<16}"
                    f"{_fmt(m['precision']):>7}"
                    f"{_fmt(m['recall']):>7}"
                    f"{_fmt(m['f1']):>7}"
                    f"{m['pred']:>7}"
                    f"{m['gold']:>7}"
                    f"{aviso}"
                )
    return "\n".join(lineas)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Score the redaction pipeline against a labelled dataset. "
            "Prints aggregate metrics only; never document text."
        )
    )
    parser.add_argument(
        "--dataset",
        default=os.environ.get("PF_GOLD_PATH") or str(CORPUS_SINTETICO),
        help="JSONL gold store (default: the synthetic corpus in tests/fixtures)",
    )
    parser.add_argument(
        "--modes",
        default=",".join(MODOS),
        help=f"comma-separated operating modes (default: {','.join(MODOS)})",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        help="also write the safe report to this path as JSON",
    )
    args = parser.parse_args()

    modos = [m.strip() for m in args.modes.split(",") if m.strip()]
    desconocidos = [m for m in modos if m not in MODOS]
    if desconocidos:
        parser.error(f"unknown mode(s): {', '.join(desconocidos)}")

    ruta = Path(args.dataset)
    ejemplos = cargar_ejemplos(ruta)
    print(f"dataset: {ruta}")
    print(f"ejemplos: {len(ejemplos)}")

    informes = [evaluar_modo(ejemplos, modo) for modo in modos]
    print(_render_tabla(informes))

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(informes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\ninforme JSON: {args.json_path}")


if __name__ == "__main__":
    main()
