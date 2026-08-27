"""Tests for the shape assertion on structured opf labels.

The structured labels used to bypass every check on the assumption that their
shape was already well defined. Measured on a real 28-page tax document, that
let PDF content-stream fragments and a monetary amount through as phone
numbers. The strings below are those artifacts — they carry no personal data.
"""

from __future__ import annotations

import os

import pytest

from server import ner_es
from server.pipeline import _opf_shape_is_plausible


# --- the artifacts observed on the real document --------------------------

@pytest.mark.parametrize(
    "artifact",
    [
        ")+(578)+(579)+(581)]",
        ")+(568)+(582)+(572)+(573)+(574)+(576)]",
        ")+(569)+(583)+(577)+(578)+(579)+(581)]",
        "(594) + (596",
        "+ 123.456,78",
    ],
)
def test_pdf_artifacts_do_not_pass_as_a_phone(artifact):
    assert _opf_shape_is_plausible("private_phone", artifact) is False


@pytest.mark.parametrize(
    "phone",
    [
        "612 04 87 39",
        "912345678",
        "+34 612 048 739",
        "0034 612048739",
        "91 111 22 33",
        "612-04-87-39",
    ],
)
def test_real_phone_numbers_still_pass(phone):
    assert _opf_shape_is_plausible("private_phone", phone) is True


@pytest.mark.parametrize(
    "not_a_phone",
    [
        "1234",              # too few digits
        "1234567890123456",  # too many
        "512345678",         # national number cannot start with 5
        "123.456,78",        # decimal amount
        "llamar al movil",   # prose
        "",
    ],
)
def test_non_phone_values_are_rejected(not_a_phone):
    assert _opf_shape_is_plausible("private_phone", not_a_phone) is False


# --- account numbers -----------------------------------------------------

@pytest.mark.parametrize(
    "account",
    [
        "1234567890123456789",
        "ES9120858371440301827465",
        "2085 8371 44 0301827465",
    ],
)
def test_real_account_numbers_pass(account):
    assert _opf_shape_is_plausible("account_number", account) is True


@pytest.mark.parametrize(
    "not_an_account",
    ["123.456,78", "saldo pendiente de la account", "123"],
)
def test_non_account_values_are_rejected(not_an_account):
    assert _opf_shape_is_plausible("account_number", not_an_account) is False


# --- email and url -------------------------------------------------------

def test_email_and_url():
    assert _opf_shape_is_plausible("private_email", "zurine@correo.example") is True
    assert _opf_shape_is_plausible("private_email", "sin arroba") is False
    assert _opf_shape_is_plausible("private_email", "a@b") is False
    assert _opf_shape_is_plausible("private_url", "https://sede.example/tramite") is True
    assert _opf_shape_is_plausible("private_url", "text con espacios") is False


def test_labels_without_a_defined_shape_always_pass():
    # secret has no characteristic shape; an unknown future label must not be
    # silently dropped either.
    assert _opf_shape_is_plausible("secret", "cualquier cosa (rara)") is True
    assert _opf_shape_is_plausible("etiqueta_futura", "?!") is True


# --- ORG default ---------------------------------------------------------

def test_org_is_disabled_by_default():
    # Policy: entity names stay in the clear so the document keeps its meaning.
    if os.environ.get("PF_NER_LABELS"):
        pytest.skip("PF_NER_LABELS overridden in this environment")
    assert "PER" in ner_es._ENABLED_LABELS
    assert "LOC" in ner_es._ENABLED_LABELS
    assert "ORG" not in ner_es._ENABLED_LABELS


# --- ORG rerouted to PER when the context says person ---------------------

@pytest.mark.parametrize(
    "text,context,expected",
    [
        # spaCy tags all-caps names as ORG; the trigger disambiguates.
        ("GARAZI ELIZONDO ZUBIZARRETA", "COMPARECENCIA DE ", "PER"),
        ("SATURNINO PELAYO GOIKOETXEA", "El compareciente ", "PER"),
        # "declaracion de" is deliberately NOT a trigger: in Spanish tax and
        # legal text it introduces a thing far more often than a person
        # ("declaración de la renta", "declaración de bienes").
        ("SATURNINO PELAYO GOIKOETXEA", "DECLARACION DE ", None),
        # Company-form marker blocks the reroute even with a trigger.
        ("Suministros Industriales Anagrama S.L.U.", "El compareciente ", None),
        # No trigger: a real institution stays dropped.
        ("Agencia Tributaria", "conforme a lo previsto en la ", None),
        # Single token carries too little evidence.
        ("Hacienda", "El compareciente ", None),
    ],
)
def test_org_is_rerouted_to_person_only_with_a_trigger(text, context, expected):
    if os.environ.get("PF_NER_LABELS"):
        pytest.skip("PF_NER_LABELS overridden in this environment")
    assert ner_es._resolve_label("ORG", text, context) == expected


def test_enabled_labels_pass_through_untouched():
    assert ner_es._resolve_label("PER", "Cualquier Nombre", "") == "PER"
    assert ner_es._resolve_label("LOC", "Algun Sitio", "") == "LOC"


def test_a_disabled_non_org_label_is_dropped():
    assert ner_es._resolve_label("MISC", "Algo Raro", "El compareciente ") is None


# --- all-caps case normalisation ------------------------------------------

def test_all_caps_runs_are_normalised_preserving_length():
    original = "COMPARECENCIA DE GARAZI ELIZONDO ZUBIZARRETA - ACTA NUMERO 12"
    normalised = ner_es.normalize_allcaps_runs(original)
    assert normalised is not None
    # Length must be preserved: offsets from the normalised view index straight
    # back into the original text.
    assert len(normalised) == len(original)
    assert "Garazi Elizondo Zubizarreta" in normalised


def test_text_without_all_caps_runs_is_not_normalised():
    assert ner_es.normalize_allcaps_runs("El compareciente acepta las condiciones") is None
    # A single all-caps word is not a run.
    assert ner_es.normalize_allcaps_runs("El DNI del interesado") is None


def test_normalisation_preserves_length_on_accented_text():
    original = "DOÑA MARÍA ÍÑIGUEZ AÑÓN COMPARECE"
    normalised = ner_es.normalize_allcaps_runs(original)
    assert normalised is not None
    assert len(normalised) == len(original)
