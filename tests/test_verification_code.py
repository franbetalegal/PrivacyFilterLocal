"""Tests for the Código Seguro de Verificación recognizer.

A CSV is not another identifier, it is a credential: with it anyone can fetch
the original document from the issuing body's website, so an "anonymised" copy
carrying its own CSV can be traded back for the original. That makes it the one
value whose presence undoes the whole redaction.

The codes below are invented. The negative cases carry the weight: a 16-32
character alphanumeric run is also a cadastral reference or a hash, which is why
this is the only recognizer that requires context rather than merely scoring it.
"""

from __future__ import annotations

import pytest

from server import recognizers_es


def _codes(text: str) -> list[str]:
    return [
        text[r.start:r.end]
        for r in recognizers_es.analyze(text)
        if r.entity_type == "ES_VERIFICATION_CODE"
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Autenticidad verificable mediante Codigo Seguro de Verificacion "
            "QT4KLM0817345522906713X en la sede electronica",
            "QT4KLM0817345522906713X",
        ),
        (
            "indicando el Código Seguro de Verificación (CSV) ABC123DEF456GHI789JK",
            "ABC123DEF456GHI789JK",
        ),
        (
            "documento cotejable en la sede electronica: ZZ8PQR4491028837465512M",
            "ZZ8PQR4491028837465512M",
        ),
    ],
)
def test_a_verification_code_is_caught_when_announced(text, expected):
    assert _codes(text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        # A cadastral reference has the same shape and must keep its own label.
        "con referencia catastral comun: 5189011DF2958H0001KE.",
        "referencia catastral 9872023VH5797S0001WX del inmueble",
        # A digest is not personal data.
        "el hash SHA256 A1B2C3D4E5F6A7B8C9D0E1F2 del fichero adjunto",
        # No announcing words anywhere: not enough to call it a credential.
        "Fdo.:______ QT4KLM0817345522906713X",
    ],
)
def test_nothing_is_caught_without_the_announcing_words(text):
    assert _codes(text) == []


def test_a_confirmed_code_is_caught_again_where_context_is_missing():
    # Measured on a real document: the code appeared twice, once under "Código
    # Seguro de Verificación" and once on the signature page next to "Fdo.:____".
    # Requiring context caught only the first, so the credential still shipped.
    # Identity settles it — the same string in the same document is the same
    # code — and that is sounder than widening the window and losing precision.
    text = (
        "Autenticidad verificable con Codigo Seguro de Verificacion "
        "QT4KLM0817345522906713X en la sede. "
        "Firma del interesado, Fdo.:_________ QT4KLM0817345522906713X"
    )
    assert _codes(text) == [
        "QT4KLM0817345522906713X",
        "QT4KLM0817345522906713X",
    ]


def test_propagation_does_not_invent_codes_of_its_own():
    # Only a value already confirmed elsewhere propagates; an unrelated run of
    # the same shape stays untouched.
    text = (
        "Codigo Seguro de Verificacion QT4KLM0817345522906713X. "
        "Referencia catastral 5189011DF2958H0001KE."
    )
    assert _codes(text) == ["QT4KLM0817345522906713X"]


def test_the_pattern_needs_both_letters_and_digits():
    # A run of only letters is a word in caps; only digits is a number.
    assert _codes("Codigo Seguro de Verificacion ABCDEFGHIJKLMNOPQRST") == []
    assert _codes("Codigo Seguro de Verificacion 12345678901234567890") == []


def test_the_label_and_the_normalisation():
    from server.pipeline import _FRIENDLY_LABEL, _ID_LABELS

    assert _FRIENDLY_LABEL["ES_VERIFICATION_CODE"] == "CSV"
    # A structured identifier: normalised by stripping non-alphanumerics, so the
    # same code written with or without separators gets the same placeholder.
    assert "ES_VERIFICATION_CODE" in _ID_LABELS


def test_it_is_the_only_recognizer_requiring_context():
    # require_context trades recall for precision and should stay exceptional:
    # every other recognizer either validates a check digit or carries a shape
    # specific enough to stand alone.
    requiring = [
        r.entity_type for r in recognizers_es._RECOGNIZERS if r.require_context
    ]
    assert requiring == ["ES_VERIFICATION_CODE"]
