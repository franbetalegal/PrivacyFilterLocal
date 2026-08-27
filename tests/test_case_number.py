"""Tests for ES_CASE_NUMBER, the court/prosecution case-number recognizer.

The number identifies the proceeding and through it the person it concerns, so
it is PII. It used to be found only when the transformer happened to read enough
of the surrounding line: measured on a real scanned court order, the same number
was detected when the page arrived as one long line and lost when it arrived as
separate lines. A deterministic recognizer removes that dependency on layout.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from server import recognizers_es
from server.pipeline import _FRIENDLY_LABEL, _ID_LABELS


def _case_numbers(text: str) -> list[str]:
    return [
        r.text for r in recognizers_es.analyze(text)
        if r.entity_type == "ES_CASE_NUMBER"
    ]


@pytest.mark.parametrize("text, expected", [
    ("N° Expediente Fiscalía: 3141/2015", ["3141/2015"]),
    ("Procedimiento Expediente 4242/2016", ["4242/2016"]),
    ("DILIGENCIAS PREVIAS 1234/2019", ["1234/2019"]),
    ("Juzgado de Instrucción nº 3, autos 45/2021", ["45/2021"]),
    ("Diligències prèvies 77/2018", ["77/2018"]),
])
def test_finds_case_numbers_next_to_their_announcing_word(text, expected):
    assert _case_numbers(text) == expected


def test_matches_only_the_number_so_the_document_still_reads():
    """The label has to survive: "N° Expediente Fiscalía: [EXPEDIENTE_1]"."""
    text = "N° Expediente Fiscalía: 3141/2015"
    hits = [r for r in recognizers_es.analyze(text)
            if r.entity_type == "ES_CASE_NUMBER"]
    assert len(hits) == 1
    assert text[hits[0].start:hits[0].end] == "3141/2015"


@pytest.mark.parametrize("text", [
    # Statutes are public law, not anybody's personal data — and they are cited
    # constantly in exactly the documents this recognizer targets.
    "conforme a la Ley Orgánica 5/2000, reguladora del procedimiento",
    "el artículo 34/2010 del reglamento del procedimiento",
    "según el Real Decreto 1774/2004 aplicable al expediente",
    "Directiva 2016/680 sobre el procedimiento sancionador",
])
def test_never_redacts_a_statute_citation(text):
    assert _case_numbers(text) == []


@pytest.mark.parametrize("text", [
    # No announcing word: a bare number/year pair is far too generic.
    "el importe de 3141/2015 unidades",
    # A day/month pair is not a case number, whatever is nearby.
    "COMPARECENCIA para el día 18/04 en el expediente",
])
def test_needs_both_a_trigger_word_and_a_four_digit_year(text):
    assert _case_numbers(text) == []


def test_the_same_number_is_redacted_everywhere_it_appears():
    """Real orders repeat the number in the header of every page."""
    text = (
        "N° Expediente Fiscalía: 3141/2015\n"
        "... cuerpo de la resolución ...\n"
        "N° Expediente Fiscalía: 3141/2015\n"
    )
    assert _case_numbers(text) == ["3141/2015", "3141/2015"]


def test_it_is_wired_into_the_pipeline_as_a_structured_identifier():
    assert _FRIENDLY_LABEL["ES_CASE_NUMBER"] == "EXPEDIENTE"
    # Normalised by stripping non-alphanumerics, so "4242/2016" and "42422016"
    # collapse to one placeholder.
    assert "ES_CASE_NUMBER" in _ID_LABELS
