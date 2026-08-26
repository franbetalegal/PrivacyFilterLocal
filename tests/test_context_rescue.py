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

CASOS_RESCATADOS = [
    ("Nicomedes Banco Salcedo", "El compareciente "),
    ("Efigenia Construccion Belaustegui", "Se persona en autos "),
    ("Baldomero Datos Urquiaga", "Firma en prueba de conformidad "),
    ("Maravillas Contrato Ibarrola", "La testigo "),
]


@pytest.mark.parametrize("span,contexto", CASOS_RESCATADOS)
def test_el_contexto_rescata_apellidos_que_chocan_con_el_lexico(span, contexto):
    # Without context the lexicon rule rejects it (that is the bug being fixed).
    assert ner_es.is_probably_false_positive(span, label="ES_NER_PER") is True
    # With a person trigger in front, it survives.
    assert (
        ner_es.is_probably_false_positive(span, label="ES_NER_PER", context=contexto)
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
def test_la_guarda_reconoce_las_denominaciones_sociales(span):
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
def test_una_empresa_con_palabra_de_boilerplate_sigue_rechazada(span):
    # "banco" / "datos" are in NEVER_IN_NAME. Because the guard blocks the
    # rescue, the lexicon rule still applies and the span is dropped — a person
    # trigger in front does not change that.
    assert (
        ner_es.is_probably_false_positive(
            span, label="ES_NER_ORG", context="El compareciente "
        )
        is True
    )


def test_el_autonomo_no_queda_atrapado_por_la_guarda_de_entidad():
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
    "basura",
    [
        "14.750,80 euros",
        "Art.4 LEC",
        "844/2016",
        "documento.pdf",
        "Domicilio:",
        "(ilegible|",
    ],
)
def test_el_rescate_no_salva_basura_estructural(basura):
    # These are rejected above the rescue point, so a trigger changes nothing.
    assert (
        ner_es.is_probably_false_positive(basura, context="El compareciente ")
        is True
    )


def test_sin_disparador_el_lexico_sigue_rechazando():
    assert (
        ner_es.is_probably_false_positive(
            "Nicomedes Banco Salcedo",
            label="ES_NER_PER",
            context="La entidad bancaria remite el ",
        )
        is True
    )


def test_un_solo_token_no_se_rescata():
    # One word carries too little evidence to override the lexicon, even in a
    # promising context.
    assert (
        ner_es.is_probably_false_positive("Banco", context="El compareciente ")
        is True
    )


# --- trigger matching itself ---------------------------------------------

@pytest.mark.parametrize(
    "contexto",
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
def test_disparadores_reconocidos(contexto):
    assert ner_es.has_person_trigger(contexto) is True


@pytest.mark.parametrize(
    "contexto",
    [
        "",
        "El presente contrato se rige por ",
        "La entidad ",
        "El importe total asciende a ",
        "Visto el expediente por el ",
    ],
)
def test_contextos_sin_disparador(contexto):
    assert ner_es.has_person_trigger(contexto) is False


def test_la_ventana_de_contexto_es_limitada():
    # A role word far away must not reach across a long clause and rescue an
    # unrelated span.
    lejano = "El compareciente" + " palabra" * 20 + " "
    assert ner_es.has_person_trigger(lejano) is False


def test_el_disparador_no_alcanza_por_encima_de_un_nombre():
    # Regression: "COMPARECENCIA DE <nombre> - ACTA NUMERO 77" rescued the
    # trailing "ACTA NUMERO 77" because the trigger was still inside the
    # character window. A trigger governs the next word or two, not a span
    # three words down the line.
    contexto = "COMPARECENCIA DE HERMENEGILDA LOSTAU BIZKARRA - "
    assert ner_es.has_person_trigger(contexto) is False
    assert (
        ner_es.is_probably_false_positive(
            "ACTA NUMERO 77", label="ES_NER_ORG", context=contexto
        )
        is True
    )


def test_el_disparador_tolera_dos_palabras_intercaladas():
    # Real legal phrasing puts a word or two between trigger and name.
    assert ner_es.has_person_trigger("Se persona en autos ") is True
    assert ner_es.has_person_trigger("Firma en prueba de conformidad ") is True


def test_el_disparador_ignora_acentos_y_mayusculas():
    assert ner_es.has_person_trigger("EL COMPARECIENTE ") is True
    assert ner_es.has_person_trigger("Doña ") is True
    assert ner_es.has_person_trigger("DOÑA ") is True


# --- segment granularity: paragraphs AND lines ---------------------------

def test_los_segmentos_incluyen_parrafo_y_lineas():
    texto = "Titular\nBittori Etxaniz Larranaga\nImporte declarado"
    segmentos = list(ner_es._iter_segments(texto))
    textos = [s for _, s in segmentos]

    # The whole paragraph, so a hard-wrapped entity keeps its context...
    assert texto in textos
    # ...and each line on its own, so unrelated adjacent fields are not merged
    # into one entity. This is what recovers a name sitting in a form header.
    assert "Bittori Etxaniz Larranaga" in textos


def test_los_offsets_de_las_lineas_apuntan_al_texto_original():
    texto = "Titular\nBittori Etxaniz Larranaga\nImporte"
    for offset, segmento in ner_es._iter_segments(texto):
        assert texto[offset:offset + len(segmento)] == segmento


def test_un_parrafo_de_una_sola_linea_no_se_duplica():
    texto = "Una sola linea sin saltos"
    segmentos = [s for _, s in ner_es._iter_segments(texto)]
    assert segmentos == [texto]


def test_las_lineas_muy_cortas_se_ignoran():
    texto = "Titular\nX\nBittori Etxaniz Larranaga"
    segmentos = [s for _, s in ner_es._iter_segments(texto)]
    assert "X" not in segmentos


# --- false positives found by reviewing a real tax document ---------------
#
# A reviewer went through every redaction the pipeline made on a 28-page AEAT
# liquidation proposal and flagged the wrong ones. All of them were NOMBRE or
# LUGAR, and all fell into three classes. The values below carry no personal
# data: they are tax vocabulary, public URLs and form abbreviations.

@pytest.mark.parametrize(
    "vocabulario_fiscal",
    [
        "Ganancia", "Ganancias", "Renta", "Dilaciones", "Donativos",
        "Minimo", "nuda", "aaee", "B.L.", "Minimo contribuyente",
        "GANANCIA PAT",
    ],
)
def test_el_vocabulario_fiscal_no_es_un_nombre(vocabulario_fiscal):
    assert (
        ner_es.is_probably_false_positive(vocabulario_fiscal, label="ES_NER_PER")
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
def test_una_url_no_es_una_persona(url):
    assert ner_es.is_probably_false_positive(url, label="ES_NER_PER") is True


@pytest.mark.parametrize("abreviatura", ["Dna", "Fdo", "D", "Sr", "Sra"])
def test_el_disparador_solo_no_es_el_nombre(abreviatura):
    # Blank form lines ("ALEGACIONES D/Dña.____") leave the honorific alone.
    # The announcing word is not the announced person.
    assert ner_es.is_probably_false_positive(abreviatura, label="ES_NER_PER") is True


@pytest.mark.parametrize("palabra", ["alquilado", "motivan", "superior", "Clave"])
def test_una_palabra_suelta_no_basta_para_ser_entidad(palabra):
    # Single capitalised words were being redacted as names and places: a past
    # participle, a verb, an adjective, and the AEAT's authentication system.
    assert ner_es.is_probably_false_positive(palabra, label="ES_NER_PER") is True
    assert ner_es.is_probably_false_positive(palabra, label="ES_NER_LOC") is True


def test_un_nombre_de_pila_suelto_sobrevive_con_disparador():
    # The one case where a lone word IS personal data: something announces it.
    assert (
        ner_es.is_probably_false_positive(
            "Nekane", label="ES_NER_PER", context="Dona "
        )
        is False
    )


@pytest.mark.parametrize("fecha", ["31/10/2025", "19/10/2022", "08/10/1974"])
def test_una_fecha_de_un_solo_token_sigue_pasando(fecha):
    # Regression: the single-token rule must not swallow dates. A token with
    # digits is a different class of evidence from a lone capitalised word.
    assert ner_es.is_probably_false_positive(fecha) is False
