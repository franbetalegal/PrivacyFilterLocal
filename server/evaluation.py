"""Precision / recall / F1 for the *whole* pipeline (Phase 7).

``opf eval`` scores the transformer alone. What the firm actually ships is the
merged output of four layers (deterministic recognizers, the custom dictionary,
opf and spaCy NER), so to answer "is it getting more precise?" we must measure
that merged output against a gold set. This module does exactly that, plus a
**leak check**: after redaction, no gold PII string may survive.

Matching is span-level (``untyped`` in opf's terms) and *relaxed by default*:
a gold span counts as found if any predicted span overlaps it. For legal
anonymization, catching the sensitive region matters more than reproducing its
exact character boundaries, and boundary jitter (a trailing space, an included
honorific) should not read as a miss. ``exact`` matching is also reported for
reference.

The evaluator is decoupled from inference: pass any ``redact_fn(text, mode) ->
result`` where ``result`` has ``detected_spans`` (each with ``start``/``end``/
``label``/``text``) and ``redacted_text``. Production passes the real pipeline;
tests inject a model-free one so CI needs no checkpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol, Sequence

from server.dataset import Example, GoldSpan


class _Result(Protocol):
    detected_spans: Sequence
    redacted_text: str


RedactFn = Callable[[str, str], _Result]


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _matches(pred_start: int, pred_end: int, gold: GoldSpan, *, exact: bool) -> bool:
    if exact:
        return pred_start == gold.start and pred_end == gold.end
    return _overlaps(pred_start, pred_end, gold.start, gold.end)


@dataclass
class _Counts:
    tp: int = 0        # predictions that hit a gold span
    fp: int = 0        # predictions that hit nothing
    fn: int = 0        # gold spans no prediction covered

    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict:
        return {
            "precision": round(self.precision(), 4),
            "recall": round(self.recall(), 4),
            "f1": round(self.f1(), 4),
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
        }


@dataclass
class Leak:
    example_index: int
    text: str
    label: str


@dataclass
class EvalReport:
    overall: dict
    exact: dict
    by_label: dict           # label -> metrics dict (typed view)
    leaks: list[Leak] = field(default_factory=list)
    examples: int = 0
    gold_spans: int = 0
    pred_spans: int = 0
    mode: str = "balanced"

    def as_dict(self) -> dict:
        return {
            "examples": self.examples,
            "gold_spans": self.gold_spans,
            "pred_spans": self.pred_spans,
            "mode": self.mode,
            "overall": self.overall,
            "exact": self.exact,
            "by_label": self.by_label,
            "leaks": [
                {"example": lk.example_index, "text": lk.text, "label": lk.label}
                for lk in self.leaks
            ],
            "leak_count": len(self.leaks),
        }


def _score_example(
    example: Example,
    predicted: Sequence,
    *,
    exact: bool,
) -> tuple[int, int, int, list[tuple[str, bool]], list[tuple[str, bool]]]:
    """Return tp, fp, fn and per-span (label, hit) lists for one example."""
    gold = list(example.spans)
    gold_hit = [False] * len(gold)
    pred_labels_hit: list[tuple[str, bool]] = []  # (pred label, hit any gold)
    for p in predicted:
        p_start, p_end = int(p.start), int(p.end)
        hit = False
        for i, g in enumerate(gold):
            if _matches(p_start, p_end, g, exact=exact):
                hit = True
                gold_hit[i] = True
        pred_labels_hit.append((str(p.label), hit))
    tp = sum(1 for _, hit in pred_labels_hit if hit)
    fp = sum(1 for _, hit in pred_labels_hit if not hit)
    fn = sum(1 for hit in gold_hit if not hit)
    gold_labels_hit = [(gold[i].label, gold_hit[i]) for i in range(len(gold))]
    return tp, fp, fn, pred_labels_hit, gold_labels_hit


def evaluate(
    examples: Iterable[Example],
    redact_fn: RedactFn,
    *,
    mode: str = "balanced",
) -> EvalReport:
    """Run ``redact_fn`` over each example and score it against the gold spans."""
    examples = list(examples)
    results = [redact_fn(ex.text, mode) for ex in examples]
    return score_examples(examples, results, mode=mode)


def score_examples(
    examples: Sequence[Example],
    results: Sequence[_Result],
    *,
    mode: str = "balanced",
) -> EvalReport:
    """Score already-computed redaction results against the gold spans.

    Split from :func:`evaluate` so callers that must run detection
    asynchronously (the API, which awaits the model per example) can gather the
    results however they like and still reuse the exact same scoring.
    """
    overall = _Counts()
    exact = _Counts()
    # Per-label typed view: precision uses predicted-label counts, recall uses
    # gold-label counts, so we track both sides separately.
    label_pred: dict[str, _Counts] = {}
    label_gold_total: dict[str, int] = {}
    label_gold_hit: dict[str, int] = {}
    leaks: list[Leak] = []
    n_examples = n_gold = n_pred = 0

    for idx, (example, result) in enumerate(zip(examples, results)):
        n_examples += 1
        n_gold += len(example.spans)
        predicted = list(result.detected_spans)
        n_pred += len(predicted)

        tp, fp, fn, pred_hits, gold_hits = _score_example(
            example, predicted, exact=False
        )
        overall.tp += tp
        overall.fp += fp
        overall.fn += fn

        etp, efp, efn, _, _ = _score_example(example, predicted, exact=True)
        exact.tp += etp
        exact.fp += efp
        exact.fn += efn

        # Typed per-label bookkeeping (relaxed matching).
        for label, hit in pred_hits:
            c = label_pred.setdefault(label, _Counts())
            if hit:
                c.tp += 1
            else:
                c.fp += 1
        for label, hit in gold_hits:
            label_gold_total[label] = label_gold_total.get(label, 0) + 1
            if hit:
                label_gold_hit[label] = label_gold_hit.get(label, 0) + 1

        # Leak check: gold PII text must not survive in the redacted output.
        redacted = getattr(result, "redacted_text", "") or ""
        for g in example.spans:
            piece = example.text[g.start:g.end]
            if piece and piece in redacted:
                leaks.append(Leak(example_index=idx, text=piece, label=g.label))

    by_label: dict[str, dict] = {}
    all_labels = set(label_pred) | set(label_gold_total)
    for label in sorted(all_labels):
        c = label_pred.get(label, _Counts())
        gold_total = label_gold_total.get(label, 0)
        gold_hit = label_gold_hit.get(label, 0)
        precision = c.precision()
        recall = gold_hit / gold_total if gold_total else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        by_label[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "pred": c.tp + c.fp,
            "gold": gold_total,
        }

    return EvalReport(
        overall=overall.as_dict(),
        exact=exact.as_dict(),
        by_label=by_label,
        leaks=leaks,
        examples=n_examples,
        gold_spans=n_gold,
        pred_spans=n_pred,
        mode=mode,
    )
