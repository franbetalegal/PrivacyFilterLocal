"""Measure detection quality against a labelled dataset.

``server/evaluation.py`` knows how to score the pipeline but nothing wires it to
a dataset, so there is no way to tell whether a change to the false-positive
filter helped. This module is that wiring: it loads a labelled corpus, runs the
real redaction pipeline over it in each operating mode, and prints per-label
precision / recall / F1.

Usage::

    .venv/bin/python -m server.baseline_cli                    # synthetic corpus
    .venv/bin/python -m server.baseline_cli --dataset data/gold.jsonl
    .venv/bin/python -m server.baseline_cli --json report.json

The default dataset is the synthetic corpus committed under ``tests/fixtures``,
which contains no real personal data. Pointing ``--dataset`` at a gold store
built from real documents is supported and safe: the report deliberately omits
``EvalReport.leaks``, whose ``text`` field holds the original PII verbatim, and
reports only how many leaks were found. Do not add example text to the output.

Interpreting the table: a label whose gold count is below
``MIN_SUPPORT`` is marked ``low support``. Its F1 is arithmetic, not
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

SYNTHETIC_CORPUS = PROJECT_DIR / "tests" / "fixtures" / "synthetic_gold.jsonl"

MODES = ("conservative", "balanced", "aggressive")

# Below this many gold spans a per-label metric is noise, not signal.
MIN_SUPPORT = 10


def load_examples(path: Path) -> list[Example]:
    """Load labelled examples from a JSONL gold store."""
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")
    return GoldStore(path).load_examples()


def _safe_report(report: EvalReport) -> dict:
    """Project an EvalReport onto the fields that are safe to print.

    Everything derived from document text is dropped. ``EvalReport.leaks``
    entries carry the original PII in ``Leak.text``; only the count survives.
    """
    by_label: dict[str, dict] = {}
    for label, metrics in report.by_label.items():
        gold = int(metrics.get("gold", 0))
        by_label[label] = {
            "precision": metrics.get("precision"),
            "recall": metrics.get("recall"),
            "f1": metrics.get("f1"),
            "pred": metrics.get("pred"),
            "gold": gold,
            "low_support": gold < MIN_SUPPORT,
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


async def _redact_all(examples: list[Example], mode: str) -> list:
    from server import inference

    results = []
    for example in examples:
        results.append(await inference.redact(example.text, mode))
    return results


def evaluate_mode(examples: list[Example], mode: str) -> dict:
    """Run the pipeline over the corpus in one mode and return a safe report."""
    results = asyncio.run(_redact_all(examples, mode))
    return _safe_report(score_examples(examples, results, mode=mode))


def _fmt(value) -> str:
    return f"{value:.3f}" if isinstance(value, (int, float)) else "-"


def _render_table(reports: list[dict]) -> str:
    lines: list[str] = []
    for report in reports:
        lines.append("")
        lines.append(
            f"mode {report['mode']}  "
            f"examples={report['examples']}  "
            f"gold={report['gold_spans']}  "
            f"pred={report['pred_spans']}  "
            f"leaks={report['leak_count']}"
        )
        overall = report["overall"]
        exact = report["exact"]
        lines.append(
            f"  overall (overlap)  P={_fmt(overall.get('precision'))} "
            f"R={_fmt(overall.get('recall'))} F1={_fmt(overall.get('f1'))}"
        )
        lines.append(
            f"  overall (exact)    P={_fmt(exact.get('precision'))} "
            f"R={_fmt(exact.get('recall'))} F1={_fmt(exact.get('f1'))}"
        )
        if report["by_label"]:
            lines.append(f"  {'label':<16}{'P':>7}{'R':>7}{'F1':>7}{'pred':>7}{'gold':>7}")
            for label in sorted(report["by_label"]):
                m = report["by_label"][label]
                note = "  low support" if m["low_support"] else ""
                lines.append(
                    f"  {label:<16}"
                    f"{_fmt(m['precision']):>7}"
                    f"{_fmt(m['recall']):>7}"
                    f"{_fmt(m['f1']):>7}"
                    f"{m['pred']:>7}"
                    f"{m['gold']:>7}"
                    f"{note}"
                )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Score the redaction pipeline against a labelled dataset. "
            "Prints aggregate metrics only; never document text."
        )
    )
    parser.add_argument(
        "--dataset",
        default=os.environ.get("PF_GOLD_PATH") or str(SYNTHETIC_CORPUS),
        help="JSONL gold store (default: the synthetic corpus in tests/fixtures)",
    )
    parser.add_argument(
        "--modes",
        default=",".join(MODES),
        help=f"comma-separated operating modes (default: {','.join(MODES)})",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        help="also write the safe report to this path as JSON",
    )
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = [m for m in modes if m not in MODES]
    if unknown:
        parser.error(f"unknown mode(s): {', '.join(unknown)}")

    path = Path(args.dataset)
    examples = load_examples(path)
    print(f"dataset: {path}")
    print(f"examples: {len(examples)}")

    reports = [evaluate_mode(examples, mode) for mode in modes]
    print(_render_table(reports))

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON report: {args.json_path}")


if __name__ == "__main__":
    main()
