"""Tests for the context rescue in ``is_probably_false_positive``.

The span-only lexicon rules reject a whole span when a single token hits
``NEVER_IN_NAME``. Measured on the synthetic corpus, that accounted for four of
six missed names, because "Banco", "Construccion", "Contrato" and "Datos" are
real Spanish surnames. The rescue keeps such a span when the preceding text
announces a person, without touching the structural rules.
"""

from __future__ import annotations

import pytest

from server import ner_es


# --- the four names the corpus showed were lost to rule 10 ----------------

RESCUED_CASES = [
    ("Nicomedes Banco Salcedo", "El compareciente "),
    ("Efigenia Construccion Belaustegui", "Se persona en autos "),
    ("Baldomero Datos Urquiaga", "Firma en prueba de conformidad "),
    ("Maravillas Contrato Ibarrola", "La testigo "),
]


@pytest.mark.parametrize("span,context", RESCUED_CASES)
def test_context_rescues_surnames_colliding_with_the_lexicon(span, context):
    # Without context the lexicon rule rejects it (that is the bug being fixed).
    assert ner_es.is_probably_false_positive(span, label="ES_NER_PER") is True
    # With a person trigger in front, it survives.
    assert (
        ner_es.is_probably_false_positive(span, label="ES_NER_PER", context=context)
        is False
    )


# --- the guard that keeps company names in the clear ----------------------

@pytest.mark.parametrize(
    "span",
    [
        "Banco Santander S.A.",
        "Suministros Industriales Anagrama S.L.U.",
        "Construcciones Peralta Sociedad Limitada",
        "Datos y Servicios Cooperativa",
    ],
)
def test_the_guard_recognises_company_names(span):
    # The guard's job is narrow: stop the rescue from firing. Whether a company
    # name ends up redacted at all is a separate policy question (the ORG entry
    # in PF_NER_LABELS), not this filter's.
    tokens = ner_es._tokens_of(span.strip().rstrip(".,;"))
    assert ner_es._looks_like_legal_entity(tokens) is True


@pytest.mark.parametrize(
    "span",
    [
        "Banco Santander S.A.",
        "Datos y Servicios Cooperativa",
    ],
)
def test_a_company_with_a_boilerplate_word_stays_rejected(span):
    # "banco" / "datos" are in NEVER_IN_NAME. Because the guard blocks the
    # rescue, the lexicon rule still applies and the span is dropped — a person
    # trigger in front does not change that.
    assert (
        ner_es.is_probably_false_positive(
            span, label="ES_NER_ORG", context="El compareciente "
        )
        is True
    )


def test_a_sole_trader_is_not_caught_by_the_entity_guard():
    # A sole trader's business name IS a natural person's name and carries no
    # company-form marker, so the rescue must still apply.
    assert (
        ner_es.is_probably_false_positive(
            "Nicomedes Banco Salcedo",
            label="ES_NER_PER",
            context="en nombre propio ",
        )
        is False
    )


# --- the rescue must not weaken the structural rules ----------------------

@pytest.mark.parametrize(
    "garbage",
    [
        "14.750,80 euros",
        "Art.4 LEC",
        "844/2016",
        "documento.pdf",
        "Domicilio:",
        "(ilegible|",
    ],
)
def test_the_rescue_does_not_save_structural_garbage(garbage):
    # These are rejected above the rescue point, so a trigger changes nothing.
    assert (
        ner_es.is_probably_false_positive(garbage, context="El compareciente ")
        is True
    )


def test_without_a_trigger_the_lexicon_still_rejects():
    assert (
        ner_es.is_probably_false_positive(
            "Nicomedes Banco Salcedo",
            label="ES_NER_PER",
            context="La entidad bancaria remite el ",
        )
        is True
    )


def test_a_single_token_is_not_rescued():
    # One word carries too little evidence to override the lexicon, even in a
    # promising context.
    assert (
        ner_es.is_probably_false_positive("Banco", context="El compareciente ")
        is True
    )


# --- trigger matching itself ---------------------------------------------

@pytest.mark.parametrize(
    "context",
    [
        "El compareciente ",
        "Doña ",
        "D. ",
        "compareció ",
        "La procuradora ",
        "se persona en autos ",
        "en representacion de ",
        "nacida el ",
    ],
)
def test_recognised_triggers(context):
    assert ner_es.has_person_trigger(context) is True


@pytest.mark.parametrize(
    "context",
    [
        "",
        "El presente contrato se rige por ",
        "La entidad ",
        "El importe total asciende a ",
        "Visto el expediente por el ",
    ],
)
def test_contexts_without_a_trigger(context):
    assert ner_es.has_person_trigger(context) is False


def test_the_context_window_is_bounded():
    # A role word far away must not reach across a long clause and rescue an
    # unrelated span.
    far_away = "El compareciente" + " word" * 20 + " "
    assert ner_es.has_person_trigger(far_away) is False


def test_a_trigger_does_not_reach_across_a_name():
    # Regression: "COMPARECENCIA DE <nombre> - ACTA NUMERO 77" rescued the
    # trailing "ACTA NUMERO 77" because the trigger was still inside the
    # character window. A trigger governs the next word or two, not a span
    # three words down the line.
    context = "COMPARECENCIA DE HERMENEGILDA LOSTAU BIZKARRA - "
    assert ner_es.has_person_trigger(context) is False
    assert (
        ner_es.is_probably_false_positive(
            "ACTA NUMERO 77", label="ES_NER_ORG", context=context
        )
        is True
    )


def test_a_trigger_tolerates_two_intervening_words():
    # Real legal phrasing puts a word or two between trigger and name.
    assert ner_es.has_person_trigger("Se persona en autos ") is True
    assert ner_es.has_person_trigger("Firma en prueba de conformidad ") is True


def test_trigger_matching_ignores_accents_and_case():
    assert ner_es.has_person_trigger("EL COMPARECIENTE ") is True
    assert ner_es.has_person_trigger("Doña ") is True
    assert ner_es.has_person_trigger("DOÑA ") is True


# --- segment granularity: paragraphs AND lines ---------------------------

def test_segments_include_the_paragraph_and_its_lines():
    text = "Titular\nBittori Etxaniz Larranaga\nImporte declarado"
    segments = list(ner_es._iter_segments(text))
    texts = [s for _, s in segments]

    # The whole paragraph, so a hard-wrapped entity keeps its context...
    assert text in texts
    # ...and each line on its own, so unrelated adjacent fields are not merged
    # into one entity. This is what recovers a name sitting in a form header.
    assert "Bittori Etxaniz Larranaga" in texts


def test_line_offsets_index_the_original_text():
    text = "Titular\nBittori Etxaniz Larranaga\nImporte"
    for offset, segment in ner_es._iter_segments(text):
        assert text[offset:offset + len(segment)] == segment


def test_a_single_line_paragraph_is_not_duplicated():
    text = "Una sola linea sin saltos"
    segments = [s for _, s in ner_es._iter_segments(text)]
    assert segments == [text]


def test_very_short_lines_are_skipped():
    text = "Titular\nX\nBittori Etxaniz Larranaga"
    segments = [s for _, s in ner_es._iter_segments(text)]
    assert "X" not in segments


# --- false positives found by reviewing a real tax document ---------------
#
# A reviewer went through every redaction the pipeline made on a 28-page AEAT
# liquidation proposal and flagged the wrong ones. All of them were NOMBRE or
# LUGAR, and all fell into three classes. The values below carry no personal
# data: they are tax vocabulary, public URLs and form abbreviations.

@pytest.mark.parametrize(
    "tax_word",
    [
        "Ganancia", "Ganancias", "Renta", "Dilaciones", "Donativos",
        "Minimo", "nuda", "aaee", "B.L.", "Minimo contribuyente",
        "GANANCIA PAT",
    ],
)
def test_tax_vocabulary_is_not_a_name(tax_word):
    assert (
        ner_es.is_probably_false_positive(tax_word, label="ES_NER_PER")
        is True
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://sede.agenciatributaria.gob.es",
        "sede.administracion.gob.es/carpeta",
        "www.agenciatributaria.es",
        "rea.redsara.es",
    ],
)
def test_a_url_is_not_a_person(url):
    assert ner_es.is_probably_false_positive(url, label="ES_NER_PER") is True


@pytest.mark.parametrize("abbreviation", ["Dna", "Fdo", "D", "Sr", "Sra"])
def test_the_trigger_alone_is_not_the_name(abbreviation):
    # Blank form lines ("ALEGACIONES D/Dña.____") leave the honorific alone.
    # The announcing word is not the announced person.
    assert ner_es.is_probably_false_positive(abbreviation, label="ES_NER_PER") is True


@pytest.mark.parametrize("word", ["alquilado", "motivan", "superior", "Clave"])
def test_a_lone_word_is_not_enough_to_be_an_entity(word):
    # Single capitalised words were being redacted as names and places: a past
    # participle, a verb, an adjective, and the AEAT's authentication system.
    assert ner_es.is_probably_false_positive(word, label="ES_NER_PER") is True
    assert ner_es.is_probably_false_positive(word, label="ES_NER_LOC") is True


def test_a_lone_first_name_survives_with_a_trigger():
    # The one case where a lone word IS personal data: something announces it.
    assert (
        ner_es.is_probably_false_positive(
            "Nekane", label="ES_NER_PER", context="Dona "
        )
        is False
    )


@pytest.mark.parametrize("date", ["31/10/2025", "19/10/2022", "08/10/1974"])
def test_a_single_token_date_still_passes(date):
    # Regression: the single-token rule must not swallow dates. A token with
    # digits is a different class of evidence from a lone capitalised word.
    assert ner_es.is_probably_false_positive(date) is False


# --- dates: birth versus transactional -----------------------------------
#
# Two classes that want opposite treatment. A date of birth identifies a person.
# The procedural and transactional dates that fill an official document are the
# substance of the matter: a capital gain is computed from the acquisition and
# transmission dates, so removing them makes the anonymised copy useless for the
# analysis it was made for. Default is to redact only births;
# PF_REDACT_ALL_DATES=1 restores blanket redaction.

@pytest.mark.parametrize(
    "date,context",
    [
        ("14 de marzo de 1979", "La interesada, nacida el "),
        ("08/10/1974", "con fecha de nacimiento "),
        ("12 de enero de 1985", "nacido el "),
        ("03/07/1990", "DOÑA MARIA, NACIDA EL "),
    ],
)
def test_a_date_of_birth_is_redacted(date, context):
    assert (
        ner_es.is_probably_false_positive(
            date, strict=False, label="private_date", context=context
        )
        is False
    )


@pytest.mark.parametrize(
    "date,context",
    [
        ("21/05/2019", "Escritura de compraventa de "),
        ("16/10/2023", "notificado en fecha "),
        ("31/12/2023", "Ejercicio cerrado a "),
        ("14 de marzo de 1979", "firmado el "),
        ("01/01/2024", "plazo que vence el "),
    ],
)
def test_a_transactional_date_is_left_alone(date, context):
    assert (
        ner_es.is_probably_false_positive(
            date, strict=False, label="private_date", context=context
        )
        is True
    )


def test_the_date_rule_needs_the_label():
    # Only the label says we are looking at a date; callers that pass none keep
    # the previous behaviour, which several existing tests rely on.
    assert ner_es.is_probably_false_positive("31/10/2025") is False


def test_a_spelled_out_birth_date_reaches_the_date_rule():
    # Regression: _is_numeral_phrase rejects "14 de marzo de 1979" outright,
    # every token being a month or a digit. With the date rule below that check,
    # a birth date never reached it.
    assert (
        ner_es.is_probably_false_positive(
            "14 de marzo de 1979",
            strict=False,
            label="private_date",
            context="nacida el ",
        )
        is False
    )
