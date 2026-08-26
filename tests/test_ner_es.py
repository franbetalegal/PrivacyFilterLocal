"""Tests for the Spanish NER layer.

The full test module is skipped when spaCy or its Spanish model isn't
installed, so CI can pass without the ~560 MB download; the pipeline layer
tests separately assert that missing spaCy degrades to an empty NER list.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import ner_es

# Only the handful of tests that actually run a spaCy pipeline need the model.
# The rest exercise is_probably_false_positive and the segment iterator, which
# are pure lexicon and string logic. Skipping the whole module on a missing
# model — as this file used to — hid 143 of its 150 tests from CI, which
# installs spaCy but not the ~500 MB language models. Those 143 cover the
# false-positive filter, the most frequently changed logic in ner_es.
requires_model = pytest.mark.skipif(
    not ner_es.is_available(),
    reason=(
        "Spanish spaCy model not installed. Install with "
        "'pip install spacy && python -m spacy download es_core_news_lg'."
    ),
)


@requires_model
def test_analyze_detects_person_but_not_bare_municipality():
    """Policy: a person is PII, a lone municipality is not.

    "Madrid" alone doesn't identify anyone, so it must not be redacted; only a
    full address is. See _is_bare_place in server/ner_es.py.
    """
    text = "Juan García Pérez comparece ante el Juzgado de Madrid."
    spans = ner_es.analyze(text)
    labels = {s.entity_type for s in spans}
    assert "ES_NER_PER" in labels
    assert not any(s.text.strip() == "Madrid" for s in spans)


@requires_model
def test_analyze_offsets_map_back_to_original_text():
    text = "El interesado, Juan García, vive en Madrid."
    for span in ner_es.analyze(text):
        assert text[span.start:span.end] == span.text


@requires_model
def test_analyze_empty_text():
    assert ner_es.analyze("") == []


@requires_model
def test_analyze_score_below_opf_and_above_low_deterministic():
    text = "Juan García firmó."
    spans = ner_es.analyze(text)
    assert spans, "Expected at least one span for a clear PER"
    for s in spans:
        # opf default weight is 0.85; deterministic postal code is 0.45.
        assert 0.5 < s.score < 0.85


def test_iter_blocks_splits_on_blank_lines():
    text = "Primer párrafo.\n\nSegundo párrafo.\n\n\n  \n\nTercero."
    blocks = list(ner_es._iter_blocks(text))
    assert [b for _, b in blocks] == [
        "Primer párrafo.",
        "Segundo párrafo.",
        "Tercero.",
    ]
    # Offsets must map back to the original text exactly.
    for start, block in blocks:
        assert text[start:start + len(block)] == block


def test_iter_blocks_single_block_when_no_blank_lines():
    text = "Un único párrafo sin saltos dobles."
    assert list(ner_es._iter_blocks(text)) == [(0, text)]


@requires_model
def test_analyze_bilingual_document_offsets_stay_correct():
    # A Spanish paragraph followed by a Catalan one, separated by a blank line.
    text = (
        "En Madrid, la Sra. María López comparece ante el Juzgado "
        "de Primera Instancia número 12.\n\n"
        "A Barcelona, la Sra. Anna Puig, resident al carrer Balmes, "
        "compareix davant el Jutjat en representació d'ACME S.L."
    )
    spans = ner_es.analyze(text)
    # Every span's offset must reconstruct the original entity text.
    for s in spans:
        assert text[s.start:s.end] == s.text
    labels = [(s.entity_type, s.text) for s in spans]
    # Person from the Spanish block AND person from the Catalan block should
    # both be present — proof that routing per block works and doesn't drop
    # coverage of the "other" language.
    persons = {t for lbl, t in labels if lbl == "ES_NER_PER"}
    assert any("María López" in p for p in persons), persons
    assert any("Anna Puig" in p for p in persons), persons


@requires_model
def test_analyze_bilingual_no_body_word_flagged_as_person():
    """Regression: Catalan common words must not survive as PER on a bilingual doc."""
    text = (
        "En Madrid, comparece Juan García.\n\n"
        "A Barcelona, la Sra. Anna Puig compareix davant el Jutjat."
    )
    spans = ner_es.analyze(text)
    person_texts = [s.text for s in spans if s.entity_type == "ES_NER_PER"]
    # 'compareix davant el' was the false positive before per-block routing.
    for t in person_texts:
        assert "compareix" not in t.lower()
        assert "davant" not in t.lower()


# --- False-positive filter --------------------------------------------------

@pytest.mark.parametrize("word", [
    # Single-token headings and filler
    "ADMITE", "NULIDAD", "INSTRUCCIÓN", "SENTENCIA",
    "Nom", "Trámit", "Plaza", "Calle", "Nombre",
    "el", "un",
    # Multi-token headings / prose (real cases from a Catalan legal writ)
    "DE EMPLAZAMIENTO PERSONA A LA QUE SE",
    "DEL EMPLAZAMIENTO Comparecer",
    "LUGAR EN EL QUE",
    "garantit amb signatura-e",
    "Codi Segur de Verificació:",
    # Form-label pattern
    "Nombre completo:",
    "Datos personales =",
])
def test_is_probably_false_positive_true(word):
    assert ner_es._is_probably_false_positive(word) is True


@pytest.mark.parametrize("phrase", [
    # Real names/orgs/locations that must survive the extended filter.
    "Juan García",
    "María López Fernández",
    "Anna Puig",
    "JUAN GARCÍA",                  # two-token all-caps still allowed
    "Juan de la Cruz",              # capitalized first token — passes
])
def test_is_probably_false_positive_false(phrase):
    assert ner_es._is_probably_false_positive(phrase) is False


@requires_model
def test_analyze_drops_heading_shaped_false_positives():
    """Regression: section headings like ADMITE/NULIDAD must not become LUGAR."""
    text = (
        "ADMITE\n\n"
        "En Madrid, la Sra. Anna Puig comparece.\n\n"
        "NULIDAD\n\n"
        "Se declara la nulidad del acto."
    )
    spans = ner_es.analyze(text)
    tagged = {s.text for s in spans}
    for junk in ("ADMITE", "NULIDAD", "Nom", "Trámit"):
        assert junk not in tagged, f"{junk!r} should not survive the filter"


# --- Public entities that should NOT be redacted ---------------------------

@pytest.mark.parametrize("phrase", [
    "Ministerio Fiscal",
    "Administración de Justicia",
    "Administració de justícia",
    "Poder Judicial",
    "Parlamento Europeo",
    "Oficina Judicial",
    "Asistencia Jurídica Gratuita",
    "Generalitat de Catalunya",
    "Cataluña",
    "Catalunya",
    "Tribunal Superior de Justicia",
    "Barcelona",
    "Madrid",
    "La Rioja",
])
def test_public_entities_are_not_pii(phrase):
    assert ner_es.is_probably_false_positive(phrase) is True


# --- File names and OCR garbage --------------------------------------------

@pytest.mark.parametrize("junk", [
    "bancaria.pdf",
    "gastos.pdf",
    "informe.docx",
    "F616YBCN9F SABLBGRVVVVM7Z06X956V7A",
])
def test_filenames_and_ocr_garbage_are_filtered(junk):
    assert ner_es.is_probably_false_positive(junk) is True


# --- Catalan single-token filler -------------------------------------------

@pytest.mark.parametrize("word", [
    "Adreça", "Rut", "Doc", "Beneficiario", "Órgano",
    "Advierto", "Dése", "LEC",
])
def test_new_catalan_fillers_are_filtered(word):
    assert ner_es.is_probably_false_positive(word) is True


# --- Trailing role-word trimming ------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("Mateo Ruiz Cano Domicilio", "Mateo Ruiz Cano"),
    ("NOELIA LARA Parte", "NOELIA LARA"),
    ("Mateo Ruiz Cano MARTIN Procurador", "Mateo Ruiz Cano MARTIN"),
    ("Mónica Llovet Perez Abogado", "Mónica Llovet Perez"),
    ("Juan García Domicilio Adreça", "Juan García"),  # peels multiple
])
def test_trim_trailing_role_words(raw, expected):
    trimmed, dropped = ner_es.trim_trailing_role_words(raw)
    assert trimmed == expected
    assert dropped == len(raw) - len(expected)


def test_trim_no_op_when_no_trailing_role():
    trimmed, dropped = ner_es.trim_trailing_role_words("Juan García Pérez")
    assert trimmed == "Juan García Pérez"
    assert dropped == 0


def test_trim_catalan_contractions():
    """d'enviament / l'hospital are article contractions, never proper nouns."""
    trimmed, dropped = ner_es.trim_trailing_role_words(
        "Dani Tipus d'enviament"
    )
    assert trimmed == "Dani"
    assert dropped == len("Dani Tipus d'enviament") - len("Dani")


def test_allcaps_real_name_survives_function_word_ratio():
    """Regression: NOELIA LARA MARTIN must NOT be filtered as a heading."""
    assert (
        ner_es.is_probably_false_positive("NOELIA LARA MARTIN") is False
    )


def test_allcaps_heading_still_rejected_via_function_word_ratio():
    """LUGAR EN EL QUE has 3/4 function-word tokens → heading."""
    assert (
        ner_es.is_probably_false_positive("LUGAR EN EL QUE") is True
    )
    assert (
        ner_es.is_probably_false_positive(
            "DE EMPLAZAMIENTO PERSONA A LA QUE SE"
        ) is True
    )


def test_public_form_label_phrases_rejected_before_trim():
    """Codi Segur de Verificació is a form label, not an entity."""
    assert (
        ner_es.is_probably_false_positive("Codi Segur de Verificació") is True
    )
    assert ner_es.is_probably_false_positive("Algorisme Hash") is True
    assert ner_es.is_probably_false_positive("Servei Comú General") is True


@pytest.mark.parametrize("junk", [
    "LEC).La",
    "753 LEC).",
    "12/2024)",     # trailing parenthesis
    "(fragment",
])
def test_structural_punctuation_marks_span_as_junk(junk):
    assert ner_es.is_probably_false_positive(junk) is True


# --- 2-token article + filler noun (regression) ----------------------------

@pytest.mark.parametrize("phrase", [
    "la Ley", "La Ley", "LA LEY",
    "el Tribunal", "el Estado",
    "los Colegios", "las Leyes",
    "el Poder",
])
def test_article_plus_filler_noun_rejected(phrase):
    assert ner_es.is_probably_false_positive(phrase) is True


@pytest.mark.parametrize("phrase", [
    "El Perelló",        # municipality name, not in the closed REGIONS set
    "La Bisbal",
])
def test_article_plus_proper_noun_not_rejected_by_the_article_rule(phrase):
    """The 'article + filler noun' rule must not fire on real place names.

    These survive that specific rule; whether they are ultimately redacted is
    decided by the bare-toponym policy, which needs a LOC label to apply.
    """
    assert ner_es.is_probably_false_positive(phrase) is False


# --- Patterns from a real 232-page notarial/banking deed -------------------
# Each group is a false-positive class the filter now recognises. Grouped so a
# regression points straight at the rule that broke.

@pytest.mark.parametrize("word", [
    # Procedural verb formulas
    "Notifíquese", "Dése", "Líbrese", "Advierto", "Dirigir", "Desistir",
    "Intervenir", "Prestar", "Tachar testigos", "Doy fe.",
])
def test_procedural_verbs_rejected(word):
    assert ner_es.is_probably_false_positive(word) is True


@pytest.mark.parametrize("word", [
    # Contract boilerplate swept up as ORG on an OCR'd bank policy
    "SEÑALADA AL INICIO DE ESTE CONTRATO",
    "CUANDO EL BANCO SEA LEGITIMO TENEDOR",
    "PLURALIDAD DE ACREDITADOS", "COMISION DE APERTURA",
    "VENCIMIENTO ANTICIPADO", "ASI COMO", "POR TANTO", "ES DECIR",
    "EN CONCRETO", "TERCERA.- INTERESES",
])
def test_contract_boilerplate_rejected(word):
    assert ner_es.is_probably_false_positive(word) is True


@pytest.mark.parametrize("junk", [
    "Bs il ll il i ie", "WY 2 A ee th pl Hd", "Ç EI", "SD dat",
    "AasongoeRD", "MeaRODeRLY 9", "qe Gay",
])
def test_ocr_debris_rejected(junk):
    assert ner_es.is_probably_false_positive(junk) is True


@pytest.mark.parametrize("amount", ["790.794,11", "3,71MM", "262.185,85 €"])
def test_amounts_and_measures_rejected(amount):
    assert ner_es.is_probably_false_positive(amount) is True


@pytest.mark.parametrize("ref", [
    "844/2016", "10/2010", "38/2003", "93/2017 de 16 de febrero",
    "753.1 LEC", "Art.4", "Art 116 del Código", "LECrim", "CNAE 622",
])
def test_law_citations_rejected(ref):
    assert ner_es.is_probably_false_positive(ref) is True


def test_real_dates_are_not_mistaken_for_law_citations():
    """Regression: a DD/MM/YYYY date has two slashes and must survive."""
    for date in ("31/10/2025", "19/10/2022", "08/10/1974"):
        assert ner_es.is_probably_false_positive(date) is False, date


@pytest.mark.parametrize("numeral", [
    "NÚMERO SIETE", "CINCO AÑOS", "CIENTO NOVENTA", "Uno-cinco",
    "VEINTISIETE DE ENERO DE",
])
def test_written_numerals_rejected(numeral):
    assert ner_es.is_probably_false_positive(numeral) is True


@pytest.mark.parametrize("desc", [
    "Parcela Urbana", "Planta Sótano", "Entresuelo", "Norte", "Sur", "Oeste",
])
def test_property_descriptions_rejected(desc):
    """Caught by the 'every token is known generic vocabulary' rule."""
    assert ner_es.is_probably_false_positive(desc) is True


@pytest.mark.parametrize("place", [
    "Barcelona", "Sabadell", "Madrid", "Barberá del Vallés", "Alcudia",
])
def test_bare_municipality_rejected_for_loc_label(place):
    """Policy: a lone toponym is not personal data, only a full address is."""
    assert ner_es.is_probably_false_positive(place, label="ES_NER_LOC") is True


@pytest.mark.parametrize("address", [
    "Calle Comadrán 5", "Paseo de Pereda 9", "Urbanización Sa Marina",
    "carrer Balmes 22",
])
def test_real_addresses_survive(address):
    """A street marker or a number means it's an address, not a bare toponym."""
    assert ner_es.is_probably_false_positive(address, label="ES_NER_LOC") is False


@pytest.mark.parametrize("name", [
    "Juan José LALLAVE CARMONA", "NOELIA LARA MARTIN",
    "Yvonne Fontquerni Coloma", "Don Manuel Piquer Belloch",
    "Eloísa López-Monís Gallego", "Mónica Llovet Perez",
    "José María de Prada Díez", "JUAN DE DIOS VALENZUELA",
])
def test_real_person_names_survive(name):
    """The whole point: none of the new rules may drop a real person."""
    assert ner_es.is_probably_false_positive(name, label="ES_NER_PER") is False
