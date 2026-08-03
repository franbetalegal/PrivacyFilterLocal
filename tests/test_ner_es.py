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

if not ner_es.is_available():
    pytest.skip(
        "Spanish spaCy model not installed. "
        "Install with 'pip install spacy && python -m spacy download es_core_news_lg'.",
        allow_module_level=True,
    )


def test_analyze_detects_person_and_location():
    text = "Juan García Pérez comparece ante el Juzgado de Madrid."
    labels = {s.entity_type for s in ner_es.analyze(text)}
    assert "ES_NER_PER" in labels
    assert "ES_NER_LOC" in labels


def test_analyze_offsets_map_back_to_original_text():
    text = "El interesado, Juan García, vive en Madrid."
    for span in ner_es.analyze(text):
        assert text[span.start:span.end] == span.text


def test_analyze_empty_text():
    assert ner_es.analyze("") == []


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
    "Plaza Mayor de Madrid",       # starts with stoplist token but 4 tokens
    "Tribunal Superior de Justicia",
    "Anna Puig",
    "Barcelona",
    "Madrid",
    "El Escorial",                  # two-token, would fail leading-stopword rule for ≥3
    "La Rioja",
    "JUAN GARCÍA",                  # two-token all-caps still allowed
    "Juan de la Cruz",              # capitalized first token — passes
])
def test_is_probably_false_positive_false(phrase):
    assert ner_es._is_probably_false_positive(phrase) is False


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
    "s/n",
    "12/2024)",
    "(fragment",
])
def test_structural_punctuation_marks_span_as_junk(junk):
    assert ner_es.is_probably_false_positive(junk) is True
