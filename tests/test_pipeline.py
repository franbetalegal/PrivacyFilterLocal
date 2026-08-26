"""Unit tests for the anonymization pipeline (merge + pseudonymization)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "privacy-filter"))

# Skip the whole module if the opf package cannot be imported at all — the
# pipeline uses RedactionResult / DetectedSpan from it (pure dataclasses, no
# model weights needed for these tests).
opf_api = pytest.importorskip("opf._api")
opf_runtime = pytest.importorskip("opf._core.runtime")

from server import ner_es, pipeline  # noqa: E402
from server.pipeline import merge_and_redact  # noqa: E402

RedactionResult = opf_api.RedactionResult
DetectedSpan = opf_runtime.DetectedSpan


@pytest.fixture(autouse=True)
def _disable_ner_by_default(monkeypatch):
    """Every existing test predates NER; stub it out to keep them deterministic.

    Individual tests below opt back in by overriding ``ner_es.analyze``.
    """
    monkeypatch.setattr(ner_es, "analyze", lambda text, **_:  [])


def _stub_opf_result(text: str, spans):
    """Build a RedactionResult as if opf had produced ``spans``.

    ``spans`` is a list of tuples ``(start, end, label, text)``.
    """
    detected = tuple(
        DetectedSpan(label=label, start=s, end=e, text=t, placeholder=label.upper())
        for (s, e, label, t) in spans
    )
    return RedactionResult(
        schema_version=1,
        summary={"span_count": len(detected), "by_label": {}},
        text=text,
        detected_spans=detected,
        redacted_text=text,   # will be recomputed by merge_and_redact
        warning=None,
    )


def test_same_person_gets_same_placeholder():
    text = "Juan García firmó el contrato. Después Juan García lo devolvió."
    opf = _stub_opf_result(text, [
        (0, 11, "private_person", "Juan García"),
        (39, 50, "private_person", "Juan García"),
    ])
    result = merge_and_redact(text, opf)
    placeholders = [s.placeholder for s in result.detected_spans]
    assert placeholders == ["[NOMBRE_1]", "[NOMBRE_1]"]
    assert "Juan García" not in result.redacted_text
    assert result.redacted_text.count("[NOMBRE_1]") == 2


def test_two_different_persons_get_distinct_tokens():
    text = "Juan García y María López asistieron."
    opf = _stub_opf_result(text, [
        (0, 11, "private_person", "Juan García"),
        (14, 25, "private_person", "María López"),
    ])
    result = merge_and_redact(text, opf)
    tokens = [s.placeholder for s in result.detected_spans]
    assert tokens == ["[NOMBRE_1]", "[NOMBRE_2]"]


def test_person_normalization_matches_case_and_whitespace():
    # Same name, different casing/whitespace → still the same token.
    text = "Juan García firmó. Luego  juan   garcía  vino de nuevo."
    opf = _stub_opf_result(text, [
        (0, 11, "private_person", "Juan García"),
        (20, 33, "private_person", "juan   garcía"),
    ])
    result = merge_and_redact(text, opf)
    assert result.detected_spans[0].placeholder == "[NOMBRE_1]"
    assert result.detected_spans[1].placeholder == "[NOMBRE_1]"


def test_deterministic_dni_beats_opf_account_number_on_overlap():
    # opf tags the DNI-shaped string as generic account_number; the
    # deterministic recognizer should win because it validated the check letter.
    text = "Referencia 12345678Z para su gestión."
    opf = _stub_opf_result(text, [
        (11, 20, "account_number", "12345678Z"),
    ])
    result = merge_and_redact(text, opf)
    labels = [s.label for s in result.detected_spans]
    assert labels == ["DNI"]
    assert result.detected_spans[0].placeholder == "[DNI_1]"


def test_deterministic_dni_added_when_opf_misses_it():
    text = "El interesado, con DNI 12345678Z, comparece."
    opf = _stub_opf_result(text, [])  # opf misses it (English-primary model)
    result = merge_and_redact(text, opf)
    labels = [s.label for s in result.detected_spans]
    assert "DNI" in labels
    assert "12345678Z" not in result.redacted_text


def test_id_normalization_across_formats():
    # Same DNI written with a hyphen and without hyphen → one shared token.
    text = "DNI 12345678Z y también 12345678-Z"
    opf = _stub_opf_result(text, [])
    result = merge_and_redact(text, opf)
    dni_spans = [s for s in result.detected_spans if s.label == "DNI"]
    assert len(dni_spans) == 2
    assert dni_spans[0].placeholder == dni_spans[1].placeholder == "[DNI_1]"


def test_iban_and_dni_and_person_together():
    text = (
        "Juan García, con DNI 12345678Z, "
        "recibirá el pago en la cuenta ES9121000418450200051332."
    )
    opf = _stub_opf_result(text, [
        (0, 11, "private_person", "Juan García"),
    ])
    result = merge_and_redact(text, opf)
    labels = {s.label for s in result.detected_spans}
    assert labels == {"NOMBRE", "DNI", "IBAN"}
    # Nothing sensitive should survive.
    for raw in ("Juan García", "12345678Z", "ES9121000418450200051332"):
        assert raw not in result.redacted_text
    # Every original PII string is replaced by a bracketed token.
    for placeholder in {s.placeholder for s in result.detected_spans}:
        assert placeholder.startswith("[") and placeholder.endswith("]")


def test_summary_counts_by_friendly_label():
    text = "DNI 12345678Z y otro DNI 00000000T"
    opf = _stub_opf_result(text, [])
    result = merge_and_redact(text, opf)
    assert result.summary["by_label"] == {"DNI": 2}
    assert result.summary["span_count"] == 2


def test_redacted_text_reconstructs_from_original_offsets():
    text = "Antes 12345678Z medio 00000000T fin"
    opf = _stub_opf_result(text, [])
    result = merge_and_redact(text, opf)
    # The non-PII fragments must remain intact.
    assert result.redacted_text.startswith("Antes ")
    assert " medio " in result.redacted_text
    assert result.redacted_text.endswith(" fin")


def test_empty_text_produces_empty_result():
    opf = _stub_opf_result("", [])
    result = merge_and_redact("", opf)
    assert result.redacted_text == ""
    assert result.detected_spans == ()
    assert result.summary["span_count"] == 0


# --- NER layer (stubbed, no spaCy dependency required) --------------------

def _ner_span(text: str, needle: str, entity_type: str, score: float = 0.7):
    """Build a fake NERSpan matching :class:`server.ner_es.NERSpan`'s shape."""
    start = text.index(needle)
    return ner_es.NERSpan(
        start=start,
        end=start + len(needle),
        entity_type=entity_type,
        score=score,
        text=needle,
    )


def test_ner_only_source_produces_spans(monkeypatch):
    """When opf misses a name but NER catches it, the pipeline still redacts."""
    text = "El testigo, Juan García, declaró ayer."
    monkeypatch.setattr(
        ner_es, "analyze",
        lambda t, **_:  [_ner_span(t, "Juan García", "ES_NER_PER")],
    )
    opf = _stub_opf_result(text, [])
    result = merge_and_redact(text, opf)
    labels = [s.label for s in result.detected_spans]
    assert "NOMBRE" in labels
    assert "Juan García" not in result.redacted_text


def test_ner_and_opf_agree_yield_one_span_and_one_token(monkeypatch):
    """Same person detected by both sources → one merged token, no duplicates."""
    text = "Juan García firmó."
    monkeypatch.setattr(
        ner_es, "analyze",
        lambda t, **_:  [_ner_span(t, "Juan García", "ES_NER_PER")],
    )
    opf = _stub_opf_result(text, [
        (0, 11, "private_person", "Juan García"),
    ])
    result = merge_and_redact(text, opf)
    # Overlap resolver keeps exactly one span; friendly label unifies both
    # sources under NOMBRE so consumers can't tell which fired.
    assert len(result.detected_spans) == 1
    assert result.detected_spans[0].label == "NOMBRE"
    assert result.detected_spans[0].placeholder == "[NOMBRE_1]"


def test_ner_person_and_opf_person_share_placeholder(monkeypatch):
    """A person mentioned twice — once by opf, once by NER — is one entity."""
    text = "Juan García firmó. Después, Juan García lo devolvió."
    second = text.index("Juan García", 12)
    monkeypatch.setattr(
        ner_es, "analyze",
        lambda t, **_:  [ner_es.NERSpan(
            start=second, end=second + 11,
            entity_type="ES_NER_PER", score=0.7, text="Juan García",
        )],
    )
    # opf catches only the first occurrence.
    opf = _stub_opf_result(text, [
        (0, 11, "private_person", "Juan García"),
    ])
    result = merge_and_redact(text, opf)
    tokens = [s.placeholder for s in result.detected_spans]
    assert tokens == ["[NOMBRE_1]", "[NOMBRE_1]"]


def test_ner_location_yields_lugar_placeholder(monkeypatch):
    text = "El juzgado de Madrid resuelve."
    monkeypatch.setattr(
        ner_es, "analyze",
        lambda t, **_:  [_ner_span(t, "Madrid", "ES_NER_LOC")],
    )
    opf = _stub_opf_result(text, [])
    result = merge_and_redact(text, opf)
    assert any(s.label == "LUGAR" and s.placeholder == "[LUGAR_1]"
               for s in result.detected_spans)


def test_ner_organization_yields_organizacion_placeholder(monkeypatch):
    text = "Comparece ACME S.L. ante el juzgado."
    monkeypatch.setattr(
        ner_es, "analyze",
        lambda t, **_:  [_ner_span(t, "ACME S.L.", "ES_NER_ORG")],
    )
    opf = _stub_opf_result(text, [])
    result = merge_and_redact(text, opf)
    assert any(s.label == "ORGANIZACION"
               and s.placeholder == "[ORGANIZACION_1]"
               for s in result.detected_spans)


def test_deterministic_dni_wins_over_ner_overlap(monkeypatch):
    """A DNI-shaped string tagged as PER by NER must still be tagged DNI.

    Deterministic validation is stronger evidence than statistical guessing.
    """
    text = "Comparece 12345678Z ante el juzgado."
    monkeypatch.setattr(
        ner_es, "analyze",
        lambda t, **_:  [_ner_span(t, "12345678Z", "ES_NER_PER")],
    )
    opf = _stub_opf_result(text, [])
    result = merge_and_redact(text, opf)
    labels = [s.label for s in result.detected_spans]
    assert labels == ["DNI"]


def test_missing_spacy_degrades_gracefully(monkeypatch):
    """If ner_es.analyze raises or returns [], the pipeline still works."""
    monkeypatch.setattr(ner_es, "analyze", lambda t, **_:  [])
    text = "El DNI 12345678Z aparece aquí."
    opf = _stub_opf_result(text, [])
    result = merge_and_redact(text, opf)
    assert any(s.label == "DNI" for s in result.detected_spans)


# --- Operating mode (fase 5) ------------------------------------------------


def test_merge_and_redact_rejects_unknown_mode():
    opf = _stub_opf_result("hi", [])
    with pytest.raises(ValueError, match="mode"):
        merge_and_redact("hi", opf, mode="turbo")


def test_conservative_mode_filters_opf_person_span_that_looks_like_heading():
    """A dodgy opf NOMBRE span (all-caps heading shape) survives in balanced
    mode (opf strict=False) but is dropped in conservative mode (opf strict=True)."""
    text = "En el juicio, DE EMPLAZAMIENTO PERSONA A LA QUE SE cita..."
    span_start = text.index("DE EMPLAZAMIENTO PERSONA A LA QUE SE")
    span_end = span_start + len("DE EMPLAZAMIENTO PERSONA A LA QUE SE")
    opf = _stub_opf_result(text, [(span_start, span_end, "private_person",
                                    "DE EMPLAZAMIENTO PERSONA A LA QUE SE")])
    balanced_result = merge_and_redact(text, opf, mode="balanced")
    conservative_result = merge_and_redact(text, opf, mode="conservative")
    # balanced keeps it (opf non-strict lets shape heuristics through)
    assert any(s.label == "NOMBRE" for s in balanced_result.detected_spans)
    # conservative drops it (opf strict → shape heuristics reject the heading)
    assert not any(s.label == "NOMBRE" for s in conservative_result.detected_spans)


def test_aggressive_mode_keeps_ner_spans_that_balanced_would_filter(monkeypatch):
    """A NER span that fails strict FP heuristics survives in aggressive mode."""
    text = "El acta DEL EMPLAZAMIENTO Comparecer se archivará."
    def fake_analyze(t, *, strict=True):
        # ner_es itself would drop this in strict mode; simulate the raw hit.
        if strict:
            return []
        return [ner_es.NERSpan(
            start=t.index("DEL EMPLAZAMIENTO Comparecer"),
            end=t.index("DEL EMPLAZAMIENTO Comparecer") + len("DEL EMPLAZAMIENTO Comparecer"),
            entity_type="ES_NER_PER", score=0.7,
            text="DEL EMPLAZAMIENTO Comparecer",
        )]
    monkeypatch.setattr(ner_es, "analyze", fake_analyze)
    opf = _stub_opf_result(text, [])

    balanced = merge_and_redact(text, opf, mode="balanced")
    aggressive = merge_and_redact(text, opf, mode="aggressive")
    assert not any(s.label == "NOMBRE" for s in balanced.detected_spans)
    assert any(s.label == "NOMBRE" for s in aggressive.detected_spans)


@pytest.mark.parametrize("mode", ["conservative", "balanced", "aggressive"])
def test_deterministic_spans_survive_every_mode(mode):
    """The DNI check-digit validator is independent of mode."""
    text = "El DNI 12345678Z acredita al interesado."
    opf = _stub_opf_result(text, [])
    result = merge_and_redact(text, opf, mode=mode)
    assert any(s.label == "DNI" for s in result.detected_spans), mode


# --- Numeric law-article references must never become DIRECCION -----------

@pytest.mark.parametrize("mode", ["conservative", "balanced", "aggressive"])
@pytest.mark.parametrize("ref", ["753", "753.1", "750", "1.234-5"])
def test_numeric_reference_never_tagged_as_direccion(ref, mode):
    """opf routinely mistags law article numbers as private_address; the
    pipeline drops them in every mode because they contain no letters."""
    text = f"Conforme al artículo {ref} LEC, se acuerda..."
    start = text.index(ref)
    opf = _stub_opf_result(text, [(start, start + len(ref), "private_address", ref)])
    result = merge_and_redact(text, opf, mode=mode)
    assert not any(s.label == "DIRECCION" for s in result.detected_spans), mode


def test_real_date_with_slashes_still_kept():
    """A date of birth must survive the numeric-reference guard.

    The guard rejects digit-and-dot runs as law references, and a DD/MM/YYYY
    date has two slashes. The context here is a birth date because that is the
    date class the pipeline redacts; see the companion test below for the class
    it deliberately leaves alone.
    """
    text = "Nacida el 31/10/2025 en Barcelona."
    start = text.index("31/10/2025")
    opf = _stub_opf_result(text, [(start, start + 10, "private_date", "31/10/2025")])
    result = merge_and_redact(text, opf, mode="balanced")
    assert any(s.label == "FECHA" for s in result.detected_spans)


def test_a_transactional_date_is_left_in_the_clear():
    """Policy: only dates of birth are redacted by default.

    An acquisition or signature date is the substance of a tax or legal matter —
    a capital gain is computed from it — so removing it makes the anonymised copy
    useless for the analysis it was made for. PF_REDACT_ALL_DATES=1 restores the
    previous behaviour when the copy is going somewhere riskier.
    """
    text = "Escritura de compraventa de 21/05/2019 ante notario."
    start = text.index("21/05/2019")
    opf = _stub_opf_result(text, [(start, start + 10, "private_date", "21/05/2019")])
    result = merge_and_redact(text, opf, mode="balanced")
    assert not any(s.label == "FECHA" for s in result.detected_spans)


def test_real_address_with_letters_still_kept():
    """Real addresses with street names must not be caught by the guard."""
    text = "Domicilio: Calle Mayor 5, 28013 Madrid."
    start = text.index("Calle Mayor 5")
    opf = _stub_opf_result(text, [
        (start, start + len("Calle Mayor 5"), "private_address", "Calle Mayor 5"),
    ])
    result = merge_and_redact(text, opf, mode="balanced")
    assert any(s.label == "DIRECCION" for s in result.detected_spans)


# --- Overlap resolution: correctness and complexity ------------------------


def _raw(start, end, source="ner", score=0.7, label="ES_NER_PER"):
    return pipeline._RawSpan(start=start, end=end, label=label,
                             text=f"s{start}", source=source, score=score)


def test_resolve_overlaps_output_is_sorted_and_disjoint():
    spans = [_raw(50, 60), _raw(0, 10), _raw(5, 15), _raw(20, 30), _raw(25, 35)]
    kept = pipeline._resolve_overlaps(spans)
    assert [s.start for s in kept] == sorted(s.start for s in kept)
    for a, b in zip(kept, kept[1:]):
        assert a.end <= b.start, f"{a} overlaps {b}"


def test_resolve_overlaps_deterministic_beats_statistical():
    """A validated deterministic span must win over an overlapping NER one."""
    det = _raw(10, 19, source="det", score=1.0, label="ES_DNI")
    ner = _raw(10, 19, source="ner", score=0.7)
    kept = pipeline._resolve_overlaps([ner, det])
    assert len(kept) == 1 and kept[0].source == "det"


def test_resolve_overlaps_prefers_longer_span():
    short = _raw(10, 15)
    long_ = _raw(10, 25)
    kept = pipeline._resolve_overlaps([short, long_])
    assert len(kept) == 1 and kept[0].end == 25


def test_resolve_overlaps_matches_bruteforce_on_random_input():
    """The O(n log n) sweep must agree with the naive O(n²) definition."""
    import random
    rnd = random.Random(1234)
    spans = []
    for _ in range(400):
        s = rnd.randrange(0, 4000)
        spans.append(_raw(s, s + rnd.randrange(1, 25),
                          source=rnd.choice(["det", "opf", "ner"]),
                          score=rnd.choice([0.45, 0.7, 0.85, 1.0])))

    def bruteforce(items):
        def priority(s):
            return (0 if s.source == "det" else 1, -(s.end - s.start), -s.score, s.start)
        kept = []
        for span in sorted(items, key=priority):
            if not any(not (span.end <= k.start or span.start >= k.end) for k in kept):
                kept.append(span)
        kept.sort(key=lambda s: s.start)
        return kept

    fast = pipeline._resolve_overlaps(spans)
    slow = bruteforce(spans)
    assert [(s.start, s.end, s.source) for s in fast] == \
           [(s.start, s.end, s.source) for s in slow]


def test_resolve_overlaps_scales_to_thousands_of_spans():
    """Guard against a regression back to quadratic behaviour."""
    import time
    spans = [_raw(i * 7, i * 7 + 5) for i in range(6000)]
    t0 = time.time()
    kept = pipeline._resolve_overlaps(spans)
    elapsed = time.time() - t0
    assert len(kept) == 6000
    assert elapsed < 2.0, f"6000 spans took {elapsed:.2f}s — quadratic again?"
