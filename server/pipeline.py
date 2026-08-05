"""Anonymization pipeline: opf model + Spanish deterministic recognizers.

Combines the contextual PII transformer (``opf``) with the regex+validator
recognizers in :mod:`server.recognizers_es`, then applies consistent
pseudonymization so that every occurrence of the same underlying entity in a
document is replaced by the same token (e.g. all mentions of "Juan García"
become ``[NOMBRE_1]``; the same DNI always becomes ``[DNI_1]``).

The public entry point is :func:`redact` which returns an
``opf._api.RedactionResult`` — the exact shape the FastAPI endpoints and the
PDF/DOCX helpers already consume, so integration is drop-in.

Merge policy (in :func:`_resolve_overlaps`):

1. Deterministic spans (with validated check digit) beat opf spans on overlap.
2. Ties broken by longer span, then higher score, then earlier start.

Rationale: an identifier whose control digit validated is *proven* to be that
identifier; the transformer may have flagged the surrounding context with a
less-specific label (e.g. ``account_number``), so we prefer the specific one.
"""
from __future__ import annotations

import unicodedata
from bisect import bisect_right
from dataclasses import dataclass

from opf._api import RedactionResult
from opf._core.runtime import DetectedSpan

from server import custom_dict, ner_es, recognizers_es


# Human-readable Spanish labels used in placeholders and DetectedSpan.label.
# opf labels are lowercased; our deterministic and NER labels come prefixed.
_FRIENDLY_LABEL: dict[str, str] = {
    "private_person": "NOMBRE",
    "private_email": "EMAIL",
    "private_phone": "TELEFONO",
    "private_address": "DIRECCION",
    "private_date": "FECHA",
    "private_url": "URL",
    "account_number": "CUENTA",
    "secret": "SECRETO",
    "ES_DNI": "DNI",
    "ES_NIE": "NIE",
    "ES_NIF_CIF": "NIF",
    "ES_IBAN": "IBAN",
    "ES_SSN": "SEG_SOCIAL",
    "ES_PHONE": "TELEFONO",
    "ES_POSTAL_CODE": "CP",
    "ES_LICENSE_PLATE": "MATRICULA",
    "ES_CADASTRAL_REF": "CATASTRO",
    "ES_CREDIT_CARD": "TARJETA",
    "ES_REGISTRY_REF": "REGISTRO",
    # Spanish NER (spaCy) — grouped with opf's equivalents for consistent
    # pseudonymization: a person detected by NER and by opf gets the same token.
    "ES_NER_PER": "NOMBRE",
    "ES_NER_LOC": "LUGAR",
    "ES_NER_ORG": "ORGANIZACION",
    "ES_NER_MISC": "OTRO",
}

# Labels whose entity value is a structured identifier: normalize by stripping
# non-alphanumerics + uppercase, so "12345678-Z" and "12345678Z" collapse.
_ID_LABELS = frozenset({
    "ES_DNI", "ES_NIE", "ES_NIF_CIF", "ES_IBAN", "ES_SSN",
    "ES_CREDIT_CARD", "ES_CADASTRAL_REF", "ES_POSTAL_CODE",
    "ES_LICENSE_PLATE", "account_number",
})


@dataclass(frozen=True)
class _RawSpan:
    start: int
    end: int
    label: str
    text: str
    source: str  # "det" or "opf"
    score: float


def _friendly(label: str) -> str:
    return _FRIENDLY_LABEL.get(label, label.upper())


def _normalize_key(text: str, label: str) -> str:
    """Canonical value used to group equal entities within one document."""
    s = unicodedata.normalize("NFKC", text).strip()
    if label in _ID_LABELS:
        s = "".join(ch for ch in s if ch.isalnum()).upper()
    else:
        s = " ".join(s.split()).casefold()
    return f"{_friendly(label)}::{s}"


def _resolve_overlaps(spans: list[_RawSpan]) -> list[_RawSpan]:
    """Drop spans that overlap a higher-priority one; return sorted by start."""
    def priority(s: _RawSpan) -> tuple:
        return (
            0 if s.source == "det" else 1,
            -(s.end - s.start),
            -s.score,
            s.start,
        )

    # Kept spans are disjoint by construction and held sorted by start, so a
    # candidate can only clash with its two neighbours: the last span starting
    # at or before it, and the first one starting after. Binary-searching those
    # makes this O(n log n) instead of the O(n²) scan-everything version — on a
    # 232-page document that's ~4k spans, where the quadratic form cost tens of
    # seconds.
    kept: list[_RawSpan] = []
    starts: list[int] = []
    for span in sorted(spans, key=priority):
        idx = bisect_right(starts, span.start) - 1
        clash = False
        if idx >= 0 and kept[idx].end > span.start:
            clash = True
        if not clash and idx + 1 < len(kept) and kept[idx + 1].start < span.end:
            clash = True
        if not clash:
            insert_at = idx + 1
            kept.insert(insert_at, span)
            starts.insert(insert_at, span.start)
    return kept


def _assign_placeholders(spans: list[_RawSpan]) -> dict[str, str]:
    """Assign ``[LABEL_N]`` — one token per unique entity value in the doc."""
    counters: dict[str, int] = {}
    tokens: dict[str, str] = {}
    for span in spans:
        key = _normalize_key(span.text, span.label)
        if key in tokens:
            continue
        friendly = _friendly(span.label)
        counters[friendly] = counters.get(friendly, 0) + 1
        tokens[key] = f"[{friendly}_{counters[friendly]}]"
    return tokens


def _build_output(
    text: str,
    spans: list[_RawSpan],
    tokens: dict[str, str],
) -> tuple[str, list[DetectedSpan]]:
    pieces: list[str] = []
    detected: list[DetectedSpan] = []
    cursor = 0
    for span in spans:
        pieces.append(text[cursor:span.start])
        placeholder = tokens[_normalize_key(span.text, span.label)]
        pieces.append(placeholder)
        detected.append(DetectedSpan(
            label=_friendly(span.label),
            start=span.start,
            end=span.end,
            text=span.text,
            placeholder=placeholder,
        ))
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces), detected


# opf labels whose spans are statistical guesses (not structurally validated)
# and should therefore go through the same false-positive / trimming filter as
# NER spans. Structured labels (private_email, private_url, secret, account
# _number) already have well-defined shapes so we let them through untouched.
_OPF_STATISTICAL_LABELS = frozenset({
    "private_person", "private_address", "private_date",
})

# Operating modes: how strictly the false-positive filter runs, per source.
# The Viterbi preset (set separately in server.inference) already influences
# how many raw spans opf emits at each mode; this table decides which of those
# survive the filter.
#
#     mode          opf strict?     opf filter applied?     ner strict?
#     conservative  True            True                    True
#     balanced      False           True                    True (default)
#     aggressive    False           False                   False
#
# "strict=True" applies the shape heuristics (all-caps ≥3, no capitalized
# token, leading preposition) as well as the source-independent rejections.
# "strict=False" drops the shape heuristics but keeps public-phrase / filename
# / garbage / single-token stoplist rejections.
_MODE_TO_OPF_FILTER: dict[str, tuple[bool, bool]] = {
    "conservative": (True, True),   # (apply_filter, strict)
    "balanced":     (True, False),
    "aggressive":   (False, False),
}
_MODE_TO_NER_STRICT: dict[str, bool] = {
    "conservative": True,
    "balanced":     True,
    "aggressive":   False,
}
_VALID_MODES = frozenset(_MODE_TO_OPF_FILTER)
DEFAULT_MODE = "balanced"


def _is_numeric_reference(text: str) -> bool:
    """True when ``text`` looks like a law-article / page / section number.

    Purely digits + separator punctuation (``. , -``) with no letters at all
    → almost always a reference (``753.1``, ``750``, ``1.234-5``), not an
    address. Slashes ``/`` are intentionally excluded so dates like
    ``31/10/2025`` are NOT caught by this rule (opf tags dates separately).
    """
    stripped = text.strip()
    if not stripped:
        return False
    return all(c.isdigit() or c in ".,-" for c in stripped) and any(
        c.isdigit() for c in stripped
    )


def _opf_raw_spans(opf_spans: tuple, mode: str) -> list[_RawSpan]:
    apply_filter, strict = _MODE_TO_OPF_FILTER[mode]
    result: list[_RawSpan] = []
    for s in opf_spans:
        text, start, end = s.text, s.start, s.end
        # Numeric-reference guard applies to *addresses* only, regardless of
        # mode: an "address" that is purely digits+dots (e.g. article 753.1
        # LEC) is a reference to a law, never a physical address.
        if s.label == "private_address" and _is_numeric_reference(text):
            continue
        if apply_filter and s.label in _OPF_STATISTICAL_LABELS:
            # Check on original first (catches public-phrase / filename /
            # single-token stoplist before trimming can distort the match).
            if ner_es.is_probably_false_positive(text, strict=strict, label=s.label):
                continue
            trimmed, dropped = ner_es.trim_trailing_role_words(text)
            if dropped:
                text = trimmed
                end -= dropped
                if end <= start:
                    continue
                if ner_es.is_probably_false_positive(text, strict=strict, label=s.label):
                    continue
        result.append(_RawSpan(
            start=start, end=end, label=s.label,
            text=text, source="opf", score=0.85,
        ))
    return result


def _deterministic_raw_spans(text: str) -> list[_RawSpan]:
    # Validated Spanish recognizers plus the user's custom dictionary. Both are
    # treated as "det": they win overlaps against the statistical models and
    # bypass the false-positive filter, because each is an explicit, checkable
    # assertion that the span is PII (a control digit validated, or the user
    # typed the term into the dictionary).
    recognitions = recognizers_es.analyze(text) + custom_dict.analyze(text)
    return [
        _RawSpan(
            start=r.start, end=r.end, label=r.entity_type,
            text=r.text, source="det", score=r.score,
        )
        for r in recognitions
    ]


def _ner_raw_spans(text: str, mode: str) -> list[_RawSpan]:
    """Statistical spaCy Spanish NER; empty if the model isn't installed."""
    strict = _MODE_TO_NER_STRICT[mode]
    return [
        _RawSpan(
            start=r.start, end=r.end, label=r.entity_type,
            text=r.text, source="ner", score=r.score,
        )
        for r in ner_es.analyze(text, strict=strict)
    ]


def merge_and_redact(
    text: str,
    opf_result: RedactionResult,
    *,
    mode: str = DEFAULT_MODE,
) -> RedactionResult:
    """Fuse opf + deterministic + NER spans and rebuild a ``RedactionResult``.

    ``mode`` selects the false-positive filter strictness per source. See
    :data:`_MODE_TO_OPF_FILTER` for the mapping. The Viterbi calibration used
    on the opf side is chosen upstream in :func:`server.inference.redact`.

    Exposed as a pure function so tests can inject a stub ``opf_result`` and
    (via monkey-patching ``ner_es.analyze``) a stub NER result too.
    """
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Unknown mode {mode!r}. Expected one of {sorted(_VALID_MODES)}."
        )
    combined = (
        _opf_raw_spans(opf_result.detected_spans, mode)
        + _deterministic_raw_spans(text)
        + _ner_raw_spans(text, mode)
    )
    merged = _resolve_overlaps(combined)
    tokens = _assign_placeholders(merged)
    redacted_text, detected = _build_output(text, merged, tokens)

    by_label: dict[str, int] = {}
    for s in detected:
        by_label[s.label] = by_label.get(s.label, 0) + 1
    summary = {
        "span_count": len(detected),
        "by_label": dict(sorted(by_label.items())),
    }
    return RedactionResult(
        schema_version=opf_result.schema_version,
        summary=summary,
        text=text,
        detected_spans=tuple(detected),
        redacted_text=redacted_text,
        warning=opf_result.warning,
    )


def redact(model, text: str, *, mode: str = DEFAULT_MODE) -> RedactionResult:
    """Full pipeline: run opf on ``text``, merge with deterministic hits.

    ``mode`` picks the operating point (see :func:`merge_and_redact`) and is
    also expected to have been applied to ``model``'s Viterbi decoder upstream
    (see :func:`server.inference.redact`).
    """
    return merge_and_redact(text, model.redact(text), mode=mode)
