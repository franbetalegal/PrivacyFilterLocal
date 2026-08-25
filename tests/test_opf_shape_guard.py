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
    "artefacto",
    [
        ")+(578)+(579)+(581)]",
        ")+(568)+(582)+(572)+(573)+(574)+(576)]",
        ")+(569)+(583)+(577)+(578)+(579)+(581)]",
        "(594) + (596",
        "+ 123.456,78",
    ],
)
def test_los_artefactos_del_pdf_no_pasan_como_telefono(artefacto):
    assert _opf_shape_is_plausible("private_phone", artefacto) is False


@pytest.mark.parametrize(
    "telefono",
    [
        "612 04 87 39",
        "912345678",
        "+34 612 048 739",
        "0034 612048739",
        "91 111 22 33",
        "612-04-87-39",
    ],
)
def test_los_telefonos_reales_siguen_pasando(telefono):
    assert _opf_shape_is_plausible("private_phone", telefono) is True


@pytest.mark.parametrize(
    "no_telefono",
    [
        "1234",              # too few digits
        "1234567890123456",  # too many
        "512345678",         # national number cannot start with 5
        "123.456,78",        # decimal amount
        "llamar al movil",   # prose
        "",
    ],
)
def test_lo_que_no_es_telefono_se_rechaza(no_telefono):
    assert _opf_shape_is_plausible("private_phone", no_telefono) is False


# --- account numbers -----------------------------------------------------

@pytest.mark.parametrize(
    "cuenta",
    [
        "1234567890123456789",
        "ES9120858371440301827465",
        "2085 8371 44 0301827465",
    ],
)
def test_las_cuentas_reales_pasan(cuenta):
    assert _opf_shape_is_plausible("account_number", cuenta) is True


@pytest.mark.parametrize(
    "no_cuenta",
    ["123.456,78", "saldo pendiente de la cuenta", "123"],
)
def test_lo_que_no_es_cuenta_se_rechaza(no_cuenta):
    assert _opf_shape_is_plausible("account_number", no_cuenta) is False


# --- email and url -------------------------------------------------------

def test_email_y_url():
    assert _opf_shape_is_plausible("private_email", "zurine@correo.example") is True
    assert _opf_shape_is_plausible("private_email", "sin arroba") is False
    assert _opf_shape_is_plausible("private_email", "a@b") is False
    assert _opf_shape_is_plausible("private_url", "https://sede.example/tramite") is True
    assert _opf_shape_is_plausible("private_url", "texto con espacios") is False


def test_las_etiquetas_sin_forma_definida_pasan_siempre():
    # secret has no characteristic shape; an unknown future label must not be
    # silently dropped either.
    assert _opf_shape_is_plausible("secret", "cualquier cosa (rara)") is True
    assert _opf_shape_is_plausible("etiqueta_futura", "?!") is True


# --- ORG default ---------------------------------------------------------

def test_org_esta_desactivada_por_defecto():
    # Policy: entity names stay in the clear so the document keeps its meaning.
    if os.environ.get("PF_NER_LABELS"):
        pytest.skip("PF_NER_LABELS overridden in this environment")
    assert "PER" in ner_es._ENABLED_LABELS
    assert "LOC" in ner_es._ENABLED_LABELS
    assert "ORG" not in ner_es._ENABLED_LABELS


# --- ORG rerouted to PER when the context says person ---------------------

@pytest.mark.parametrize(
    "texto,contexto,esperado",
    [
        # spaCy tags all-caps names as ORG; the trigger disambiguates.
        ("HERMENEGILDA LOSTAU BIZKARRA", "COMPARECENCIA DE ", "PER"),
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
def test_org_se_reencamina_a_persona_solo_con_disparador(texto, contexto, esperado):
    if os.environ.get("PF_NER_LABELS"):
        pytest.skip("PF_NER_LABELS overridden in this environment")
    assert ner_es._resolve_label("ORG", texto, contexto) == esperado


def test_las_etiquetas_activas_pasan_sin_tocar():
    assert ner_es._resolve_label("PER", "Cualquier Nombre", "") == "PER"
    assert ner_es._resolve_label("LOC", "Algun Sitio", "") == "LOC"


def test_una_etiqueta_desactivada_que_no_es_org_se_descarta():
    assert ner_es._resolve_label("MISC", "Algo Raro", "El compareciente ") is None


# --- all-caps case normalisation ------------------------------------------

def test_normaliza_las_rachas_en_mayusculas_conservando_longitud():
    original = "COMPARECENCIA DE HERMENEGILDA LOSTAU BIZKARRA - ACTA NUMERO 77"
    normalizado = ner_es.normalize_allcaps_runs(original)
    assert normalizado is not None
    # Length must be preserved: offsets from the normalised view index straight
    # back into the original text.
    assert len(normalizado) == len(original)
    assert "Hermenegilda Lostau Bizkarra" in normalizado


def test_no_normaliza_lo_que_no_tiene_rachas_en_mayusculas():
    assert ner_es.normalize_allcaps_runs("El compareciente acepta las condiciones") is None
    # A single all-caps word is not a run.
    assert ner_es.normalize_allcaps_runs("El DNI del interesado") is None


def test_la_normalizacion_conserva_longitud_en_texto_acentuado():
    original = "DOÑA MARÍA ÍÑIGUEZ AÑÓN COMPARECE"
    normalizado = ner_es.normalize_allcaps_runs(original)
    assert normalizado is not None
    assert len(normalizado) == len(original)
