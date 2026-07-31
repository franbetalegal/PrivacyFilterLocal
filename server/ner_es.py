"""Statistical Spanish NER via spaCy — an added recall layer on top of opf.

The opf model is documented as "primarily English" (see its own model card at
``privacy-filter/README.md``), so it under-detects Spanish person names,
locations and organisations in the kind of long, formal text that legal writs
produce. This module runs spaCy's Spanish pipeline in parallel and produces
spans for PER/LOC/ORG that the merger in :mod:`server.pipeline` fuses with the
opf and deterministic detections.

Why spaCy directly instead of Microsoft Presidio's ``SpacyRecognizer``:
    The pipeline already owns a well-tested overlap resolver and consistent
    pseudonymizer (see :mod:`server.pipeline`). Wrapping spaCy through Presidio
    would duplicate merge/anonymization logic. We keep spaCy as a bare NER
    layer and let our own merger arbitrate priorities.

The spaCy model is loaded lazily and cached; if spaCy or the model isn't
installed the module logs a one-time warning and returns an empty list, so
the app degrades gracefully to opf + deterministic recognizers only.

Configuration via environment variables:
    - ``PF_NER_MODEL``   Model name (default: ``es_core_news_lg``).
    - ``PF_NER_LABELS``  Comma-separated entity types to keep (default:
      ``PER,LOC,ORG``). Add ``MISC`` if the domain benefits from it.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("privacy_filter.ner_es")

_MODEL_NAME = os.environ.get("PF_NER_MODEL", "es_core_news_lg")
_ENABLED_LABELS = frozenset(
    label.strip() for label in os.environ.get("PF_NER_LABELS", "PER,LOC,ORG").split(",")
    if label.strip()
)

# The score is intentionally lower than opf (0.85) so opf-preferred boundaries
# win on overlap ties, and higher than the unvalidated deterministic entities
# (e.g. ES_POSTAL_CODE=0.45) so a well-identified name doesn't lose to a
# generic 5-digit match on tied offsets.
_NER_BASELINE_SCORE = 0.7

_nlp = None
_load_lock = threading.Lock()
_load_error: Optional[str] = None


@dataclass(frozen=True)
class NERSpan:
    """One PER/LOC/ORG match produced by spaCy."""

    start: int
    end: int
    entity_type: str
    score: float
    text: str


def _load() -> None:
    """Load and cache the spaCy pipeline. No-op after the first call."""
    global _nlp, _load_error
    if _nlp is not None or _load_error is not None:
        return
    with _load_lock:
        if _nlp is not None or _load_error is not None:
            return
        try:
            import spacy
        except ImportError as exc:
            _load_error = f"spaCy not installed: {exc}"
            logger.warning(
                "Spanish NER disabled: %s. Install with 'pip install spacy'.",
                exc,
            )
            return
        try:
            # Disable components not needed for NER to shave load time and RAM.
            _nlp = spacy.load(
                _MODEL_NAME,
                disable=["parser", "attribute_ruler", "lemmatizer"],
            )
        except OSError as exc:
            _load_error = str(exc)
            logger.warning(
                "Spanish NER disabled: model %r is not installed. "
                "Install with 'python -m spacy download %s'.",
                _MODEL_NAME, _MODEL_NAME,
            )
            return
        logger.info("Loaded Spanish NER model: %s", _MODEL_NAME)


def is_available() -> bool:
    """Return True when the NER layer will actually contribute spans."""
    _load()
    return _nlp is not None


def analyze(text: str) -> list[NERSpan]:
    """Return spaCy NER spans over ``text`` filtered to enabled entity types."""
    if not text:
        return []
    _load()
    if _nlp is None:
        return []
    doc = _nlp(text)
    results: list[NERSpan] = []
    for ent in doc.ents:
        if ent.label_ not in _ENABLED_LABELS:
            continue
        results.append(NERSpan(
            start=ent.start_char,
            end=ent.end_char,
            entity_type=f"ES_NER_{ent.label_}",
            score=_NER_BASELINE_SCORE,
            text=ent.text,
        ))
    return results
