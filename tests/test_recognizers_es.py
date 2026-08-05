"""Unit tests for the Spanish deterministic recognizers."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import recognizers_es
from server.recognizers_es import (
    analyze,
    validate_cif,
    validate_dni,
    validate_iban,
    validate_luhn,
    validate_nie,
    validate_ss,
)


# --- validate_dni ---------------------------------------------------------

@pytest.mark.parametrize("value", [
    "12345678Z",          # 12345678 % 23 = 14 → Z
    "00000000T",          # 0 % 23 = 0 → T
    "12345678-Z",         # tolerates hyphen
    "12345678 z",         # tolerates lowercase and space
])
def test_dni_accepts_valid(value):
    assert validate_dni(value) is True


@pytest.mark.parametrize("value", [
    "12345678A",          # wrong control letter
    "12345678I",          # I is not in the allowed letter set at all
    "1234567Z",           # too short
    "123456789Z",         # too long
    "1234567AZ",          # non-digit in body
    "",
])
def test_dni_rejects_invalid(value):
    assert validate_dni(value) is False


# --- validate_nie ---------------------------------------------------------

@pytest.mark.parametrize("value", [
    "X0000001R",          # 00000001 % 23 = 1 → R
    "Y0000001S",          # 10000001 % 23 = 15 → S
    "Z0000001Y",          # 20000001 % 23 = 6 → Y
])
def test_nie_accepts_valid(value):
    assert validate_nie(value) is True


@pytest.mark.parametrize("value", [
    "X0000001Z",          # wrong letter
    "A0000001R",          # invalid prefix
    "X000001R",           # too short
])
def test_nie_rejects_invalid(value):
    assert validate_nie(value) is False


# --- validate_cif ---------------------------------------------------------

def test_cif_accepts_valid_digit_control():
    # A + 3900001 → sum=17 → control_digit=3; A demands digit → "A39000013".
    assert validate_cif("A39000013") is True


def test_cif_accepts_valid_letter_control():
    # P + 1234567 → doubled+summed: 2+2+6+4+(1+0)+6+(1+4)=26, control_digit=4,
    # letter table "JABCDEFGHI"[4]="D"; P forces letter-only control.
    assert validate_cif("P1234567D") is True


@pytest.mark.parametrize("value", [
    "A39000010",          # wrong control
    "I12345678",          # invalid first letter (I not allowed)
    "A3900001",           # too short
    "AA9000013",          # non-digit body
])
def test_cif_rejects_invalid(value):
    assert validate_cif(value) is False


# --- validate_iban --------------------------------------------------------

@pytest.mark.parametrize("value", [
    "ES9121000418450200051332",              # well-known valid Spanish IBAN
    "ES91 2100 0418 4502 0005 1332",         # tolerates spaces
    "es9121000418450200051332",              # case-insensitive
    "DE89370400440532013000",                # non-Spanish IBAN also validates
])
def test_iban_accepts_valid(value):
    assert validate_iban(value) is True


@pytest.mark.parametrize("value", [
    "ES9121000418450200051333",              # last digit tampered
    "ES91",                                  # too short
    "ES91210004184502000513320",             # extra digit → mod-97 fails
])
def test_iban_rejects_invalid(value):
    assert validate_iban(value) is False


# --- validate_ss ----------------------------------------------------------

def test_ss_accepts_valid_generated():
    body = "2812345678"
    control = int(body) % 97
    ss = body + f"{control:02d}"
    assert validate_ss(ss) is True
    assert validate_ss(f"{ss[:2]} {ss[2:10]} {ss[10:]}") is True   # spaces
    assert validate_ss(f"{ss[:2]}/{ss[2:10]}/{ss[10:]}") is True   # slashes


def test_ss_rejects_tampered_control():
    body = "2812345678"
    control = (int(body) % 97 + 1) % 100  # wrong control
    ss = body + f"{control:02d}"
    assert validate_ss(ss) is False


@pytest.mark.parametrize("value", [
    "281234567800XX",     # non-digit
    "281234567800000",    # too long
    "28123456780",        # too short
])
def test_ss_rejects_invalid(value):
    assert validate_ss(value) is False


# --- validate_luhn --------------------------------------------------------

@pytest.mark.parametrize("value", [
    "4111111111111111",           # Visa test
    "5555555555554444",           # Mastercard test
    "378282246310005",            # AMEX test (15 digits)
    "4111 1111 1111 1111",        # spaces tolerated
    "4111-1111-1111-1111",        # hyphens tolerated
])
def test_luhn_accepts_valid(value):
    assert validate_luhn(value) is True


@pytest.mark.parametrize("value", [
    "4111111111111112",           # last digit tampered
    "123",                        # too short
    "4111111111111111X",          # non-digit
])
def test_luhn_rejects_invalid(value):
    assert validate_luhn(value) is False


# --- analyze() end-to-end -------------------------------------------------

def test_analyze_finds_dni_cif_iban():
    text = (
        "El DNI 12345678Z pertenece a la empresa A39000013. "
        "Envío a la cuenta IBAN ES9121000418450200051332."
    )
    hits = analyze(text)
    labels = {h.entity_type for h in hits}
    assert "ES_DNI" in labels
    assert "ES_NIF_CIF" in labels
    assert "ES_IBAN" in labels
    # Positions must map back to the exact substring
    for h in hits:
        assert text[h.start:h.end] == h.text


def test_analyze_context_boosts_score():
    with_context = analyze("Mi DNI 12345678Z")
    without_context = analyze("Referencia 12345678Z")
    d1 = next(h for h in with_context if h.entity_type == "ES_DNI")
    d2 = next(h for h in without_context if h.entity_type == "ES_DNI")
    assert d1.score > d2.score


def test_analyze_ignores_invalid_dni_shaped_string():
    # 12345678A has the DNI shape but the wrong control letter.
    hits = analyze("La referencia 12345678A no es un DNI")
    assert not any(h.entity_type == "ES_DNI" for h in hits)


def test_analyze_finds_credit_card_luhn_only():
    text = "Tarjeta 4111 1111 1111 1111 y no-válida 4111 1111 1111 1112"
    cards = [h for h in analyze(text) if h.entity_type == "ES_CREDIT_CARD"]
    assert len(cards) == 1
    assert cards[0].text == "4111 1111 1111 1111"


def test_analyze_finds_phone_and_postal_code():
    text = "Llamar al +34 612 345 678, CP 28013"
    labels = {h.entity_type for h in analyze(text)}
    assert "ES_PHONE" in labels
    assert "ES_POSTAL_CODE" in labels


def test_analyze_empty_text():
    assert analyze("") == []


def test_analyze_positions_are_original_offsets():
    text = "  12345678Z  "
    hit = next(h for h in analyze(text) if h.entity_type == "ES_DNI")
    assert text[hit.start:hit.end] == "12345678Z"


# --- Registry / cadastral identifiers -------------------------------------

@pytest.mark.parametrize("ref", [
    "Tomo 4097", "Libro 916", "Folio 64", "Hoja 286", "Inscripción 4",
    "Finca Registral 817", "Sección 1", "Foli 12", "Llibre 5",
])
def test_registry_references_detected(ref):
    """Registry locators must be caught by the *deterministic* layer.

    They deliberately bypass the NER false-positive filter: "tomo"/"libro"/
    "folio"/"hoja" are in the boilerplate lexicon (they're noise as bare
    words), so only a validated match keeps them.
    """
    text = f"Consta en el {ref} del Registro de la Propiedad."
    hits = [h for h in analyze(text) if h.entity_type == "ES_REGISTRY_REF"]
    assert hits, f"{ref!r} not detected"
    assert hits[0].text == ref


@pytest.mark.parametrize("ref", [
    "9464302DF2996S0004RR", "9019304EE0191N0001HE", "4256807DF2945E0001GD",
])
def test_cadastral_references_detected(ref):
    hits = [h for h in analyze(f"Referencia catastral {ref}.")
            if h.entity_type == "ES_CADASTRAL_REF"]
    assert hits and hits[0].text == ref


def test_registry_reference_needs_a_number():
    """A bare 'Tomo' with no number is noise, not an identifier."""
    hits = [h for h in analyze("El tomo consta archivado.")
            if h.entity_type == "ES_REGISTRY_REF"]
    assert hits == []
