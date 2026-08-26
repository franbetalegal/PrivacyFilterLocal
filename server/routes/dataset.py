"""Gold set + evaluation (Phase 7 — measure detection quality)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from server import inference
from server import messages

router = APIRouter()


class CaptureIn(BaseModel):
    """One reviewed example to append to the local gold set (Phase 7)."""
    text: str
    spans: list[dict]


class EvaluateIn(BaseModel):
    """Request to score the whole gold set in a given operating mode."""
    mode: str = "balanced"


@router.post("/api/dataset/capture")
def api_dataset_capture(payload: CaptureIn) -> dict:
    """Append one reviewed example to the local gold set.

    Called from the human-in-the-loop review flow when the user opts to keep a
    curated result as an evaluation example. The spans are the reviewer's final,
    corrected list — the ground truth we score against later. Stored locally and
    git-ignored (it contains real PII).
    """
    from server import dataset

    return dataset.get_store().append_example(payload.text, payload.spans)


@router.get("/api/dataset/stats")
def api_dataset_stats() -> dict:
    """How many examples/spans the gold set holds, broken down by label."""
    from server import dataset

    return dataset.get_store().stats()


@router.post("/api/dataset/evaluate")
async def api_dataset_evaluate(payload: EvaluateIn) -> dict:
    """Score the whole gold set through the real pipeline in ``mode``.

    Runs detection on every stored example and reports precision/recall/F1
    (overall and per label) plus any leaks — gold PII strings that survived
    redaction. This is the objective "is it getting more precise?" answer.
    """
    from server import dataset, evaluation

    examples = dataset.get_store().load_examples()
    if not examples:
        return {"examples": 0, "detail": messages.message(messages.GOLD_SET_EMPTY)}
    # Detection must go through the model, which is async and serialized on the
    # inference pool; gather results first, then score synchronously.
    results = []
    for ex in examples:
        results.append(await inference.redact(ex.text, mode=payload.mode))
    report = evaluation.score_examples(examples, results, mode=payload.mode)
    return report.as_dict()
