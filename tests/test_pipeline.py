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

from server import pipeline  # noqa: E402
from server.pipeline import merge_and_redact  # noqa: E402

RedactionResult = opf_api.RedactionResult
DetectedSpan = opf_runtime.DetectedSpan


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
