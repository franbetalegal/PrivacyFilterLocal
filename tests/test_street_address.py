"""Tests for the deterministic Spanish street-address recognizer.

Written from a real failure. On a 28-page tax document the taxpayer's property
address survived redaction in full ("Cl. Sant Ferran, 0176 de Sabadell"), and
the second address had only its street name removed — labelled NOMBRE, because
"Frederic Soler" is a person's name used as a street name. Leaving addresses to
the statistical layers was losing them.

Every address below is invented. The negative cases are the ones that matter
most: a street-type marker on its own appears constantly in Spanish
administrative prose, and the tax agency's own login system is called Cl@ve.
"""

from __future__ import annotations

import pytest

from server import recognizers_es


def _addresses(text: str) -> list[str]:
    return [
        text[r.start:r.end]
        for r in recognizers_es.analyze(text)
        if r.entity_type == "ES_STREET_ADDRESS"
    ]


# --- the shapes that must be caught, with exact boundaries ----------------

@pytest.mark.parametrize(
    "text,expected",
    [
        # Marker + name + number + municipality after "de".
        (
            "Vivienda situada en Cl. Sant Ferran, 0176 de Sabadell.",
            "Cl. Sant Ferran, 0176 de Sabadell",
        ),
        # "s/n" is the second signal when a plot has no number.
        (
            "terreno urbano situado en Cl. Frederic Soler s/n de Sabadell",
            "Cl. Frederic Soler s/n de Sabadell",
        ),
        # Lowercase marker, several connectors inside the name, floor, door,
        # postcode and municipality.
        (
            "domicilio en calle Herreria de los Olmos 14, 3o B, 28912 Leganes",
            "calle Herreria de los Olmos 14, 3o B, 28912 Leganes",
        ),
        # The name may start with a connector.
        (
            "avenida del Molino Viejo 208, 41013 Sevilla",
            "avenida del Molino Viejo 208, 41013 Sevilla",
        ),
        # All caps, no "de" before the municipality — the postcode carries it.
        (
            "Oficina de Gestion Tributaria CL BOSCH, 1 08202 SABADELL",
            "CL BOSCH, 1 08202 SABADELL",
        ),
        (
            "notificaciones en Pza. Mayor 3, 2o A",
            "Pza. Mayor 3, 2o A",
        ),
    ],
)
def test_addresses_are_caught_with_exact_boundaries(text, expected):
    assert _addresses(text) == [expected]


# --- extraction artifacts ------------------------------------------------

def test_a_comma_between_marker_and_name_is_tolerated():
    # PDF extraction produced "Cl., Frederic Soler" in one of three occurrences
    # of the same address; requiring plain whitespace lost exactly that one.
    text = "terreno urbano en Cl., Frederic Soler s/n de Sabadell, con"
    assert _addresses(text) == ["Cl., Frederic Soler s/n de Sabadell"]


def test_a_line_break_inside_the_address_is_tolerated():
    # Hard-wrapped lines put breaks inside entities; the address must survive.
    text = "situado en Cl. Frederic \nSoler s/n de Sabadell"
    assert _addresses(text) == ["Cl. Frederic \nSoler s/n de Sabadell"]


# --- what must NOT be caught ---------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        # Cl@ve is the tax agency's authentication system. A marker-only pattern
        # matched it, which is why a house number is always required.
        "Con Cl@ve PIN o certificado electronico",
        "Este servicio se obtiene con Clave PIN",
        # A marker opening an ordinary sentence.
        "Plaza vacante en el tribunal",
        "La calle es competencia municipal",
        "Ronda de conversaciones sin acuerdo",
        "El paseo de los ninos fue agradable",
        # Marker and a name but no number: not enough evidence.
        "se cita en Calle Mayor para la vista",
        # A number but no marker.
        "el expediente 512 de 2023",
    ],
)
def test_no_address_without_both_signals(text):
    assert _addresses(text) == []


def test_a_bare_number_is_not_taken_as_a_floor():
    # With a bare number accepted as a floor, three repetitions swallowed a
    # postcode in pieces ("082" + "02") and truncated the span.
    text = "CL BOSCH, 1 08202 SABADELL"
    assert _addresses(text) == ["CL BOSCH, 1 08202 SABADELL"]


def test_a_door_letter_does_not_eat_the_municipality():
    # A lone capital is a door only when no letter follows it; otherwise it took
    # the first letter of the town, leaving "08202 S".
    text = "Pza. Mayor 3, 08202 Sabadell"
    assert _addresses(text) == ["Pza. Mayor 3, 08202 Sabadell"]


# --- integration: the label and the overlap it must win -------------------

def test_the_address_wins_over_a_person_span_on_the_street_name():
    from server.pipeline import _FRIENDLY_LABEL

    # Deterministic spans beat statistical ones, which is what stops a street
    # named after a person from being labelled NOMBRE.
    assert _FRIENDLY_LABEL["ES_STREET_ADDRESS"] == "DIRECCION"


def test_the_score_sits_below_the_validated_identifiers():
    # A shape match is strong evidence, not proof: it must not outrank an
    # entity whose check digit was verified.
    by_type = {r.entity_type: r.score for r in recognizers_es._RECOGNIZERS}
    assert by_type["ES_STREET_ADDRESS"] < by_type["ES_DNI"]
    assert by_type["ES_STREET_ADDRESS"] < by_type["ES_IBAN"]
    # ...but above the unvalidated ones it must absorb, so a full address wins
    # the overlap against the bare postal code inside it.
    assert by_type["ES_STREET_ADDRESS"] > by_type["ES_POSTAL_CODE"]
    assert by_type["ES_STREET_ADDRESS"] > by_type["ES_PHONE"]
