"""Generate the synthetic labelled corpus used as the detection baseline.

Why this exists
---------------
``server/evaluation.py`` can score the pipeline against a labelled dataset, but
nothing in the repository provides one: the default gold store
(``data/gold.jsonl``) starts empty and only fills up through human review of
real documents, which cannot be committed. Without a dataset there is no way to
tell whether a change to the false-positive filter helped or hurt, so tuning it
is guesswork.

This module writes a fully synthetic corpus that can live in git. Every person
name, company, address, phone and email below is invented. Identifiers that
carry a check digit (DNI, NIE, IBAN, Seguridad Social) are computed here rather
than typed by hand, and every one is asserted against the project's own
validators in ``server.recognizers_es`` — so the corpus also cross-checks those
validators.

The corpus deliberately over-samples the cases where the current filter is
suspected to misfire, because that is what we need to measure:

- ``negativo``: text that must NOT be redacted (legal boilerplate, court and
  institution names, statute citations, all-caps headings, amounts, company
  names, case-file references, bare toponyms).
- ``positivo``: text that MUST be redacted (invented people in legal context,
  full addresses, valid-checksum identifiers, birth dates, contact details).
- ``trampa``: the interesting group. Surnames that collide with entries in
  ``lexicon_es.NEVER_IN_NAME`` ("Banco", "Construcción" are real Spanish
  surnames), names inside all-caps headings, and toponyms used as surnames.
  Rule 10 of ``is_probably_false_positive`` rejects a whole span when any single
  token hits that lexicon, so this group quantifies the damage.

Span offsets are never written by hand. Each example is authored with the PII
wrapped in guillemets and an explicit label list, e.g.::

    ("El compareciente «Íñigo Vallespín Arrondo» manifiesta", ["NOMBRE"])

``_parse`` strips the markers and derives the character offsets, so the corpus
cannot drift out of sync with its own annotations.

Run with::

    .venv/bin/python tests/fixtures/generar_gold_sintetico.py

which rewrites ``gold_sintetico.jsonl`` next to this file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.recognizers_es import (  # noqa: E402
    validate_dni,
    validate_iban,
    validate_nie,
    validate_ss,
)

OPEN, CLOSE = "«", "»"  # « »

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def dni(numero: int) -> str:
    """Build a syntactically valid DNI from an 8-digit number."""
    return f"{numero:08d}{_DNI_LETTERS[numero % 23]}"


def nie(prefijo: str, numero: int) -> str:
    """Build a syntactically valid NIE (X/Y/Z + 7 digits + letter)."""
    base = int(f"{'XYZ'.index(prefijo)}{numero:07d}")
    return f"{prefijo}{numero:07d}{_DNI_LETTERS[base % 23]}"


def iban_es(banco: str, sucursal: str, dc_cuenta: str, cuenta: str) -> str:
    """Build a valid Spanish IBAN, computing the ISO 13616 mod-97 check."""
    bban = f"{banco}{sucursal}{dc_cuenta}{cuenta}"
    # ES00 placeholder moved to the end, letters mapped to numbers (E=14, S=28).
    rearranged = f"{bban}142800"
    check = 98 - (int(rearranged) % 97)
    return f"ES{check:02d}{bban}"


def seguridad_social(provincia: str, numero: str) -> str:
    """Build a valid Seguridad Social number with its mod-97 control digits."""
    base = f"{provincia}{numero}"
    control = int(base) % 97
    return f"{base}{control:02d}"


# Identifiers are generated, then verified with the project's own validators so
# a broken generator cannot silently poison the corpus.
DNI_1 = dni(41_820_573)
DNI_2 = dni(7_493_162)
NIE_1 = nie("Y", 4_812_907)
IBAN_1 = iban_es("2085", "8371", "44", "0301827465")
SS_1 = seguridad_social("28", "1049783625"[:8])

for _valor, _validador, _nombre in (
    (DNI_1, validate_dni, "DNI_1"),
    (DNI_2, validate_dni, "DNI_2"),
    (NIE_1, validate_nie, "NIE_1"),
    (IBAN_1, validate_iban, "IBAN_1"),
    (SS_1, validate_ss, "SS_1"),
):
    if not _validador(_valor):
        raise AssertionError(f"{_nombre}={_valor!r} rejected by {_validador.__name__}")


# --- the corpus -----------------------------------------------------------
#
# Each entry: (grupo, texto_con_marcadores, [etiqueta por marcador en orden]).
# A negative example simply carries no markers and an empty label list.

CORPUS: list[tuple[str, str, list[str]]] = [
    # ---------------- negativos: nada que tachar ----------------
    (
        "negativo",
        "El presente contrato se rige por lo dispuesto en el articulo 1256 del "
        "Codigo Civil y en la Ley 3/2004, de 29 de diciembre, de lucha contra "
        "la morosidad en las operaciones comerciales.",
        [],
    ),
    (
        "negativo",
        "Visto el expediente por el Juzgado de Primera Instancia numero 4 de "
        "Alcala de Henares, y oido el Ministerio Fiscal, procede acordar lo "
        "siguiente.",
        [],
    ),
    (
        "negativo",
        "CLAUSULA SEXTA. OBLIGACIONES DE LAS PARTES",
        [],
    ),
    (
        "negativo",
        "El importe total asciende a 14.750,80 euros, IVA del 21 % incluido, "
        "con un recargo del 5 % por demora superior a treinta dias naturales.",
        [],
    ),
    (
        "negativo",
        "La entidad Suministros Industriales Anagrama S.L.U. figura inscrita en "
        "el Registro Mercantil con el numero de asiento 118.409.",
        [],
    ),
    (
        "negativo",
        "Notifiquese la presente resolucion a las partes y librese testimonio "
        "para su union a los autos, certificando lo actuado.",
        [],
    ),
    (
        "negativo",
        "Procedimiento ordinario 512/2023, pieza separada de medidas cautelares, "
        "tramitada conforme al articulo 730 de la Ley de Enjuiciamiento Civil.",
        [],
    ),
    (
        "negativo",
        "La competencia territorial corresponde a los tribunales de Barcelona "
        "por remision expresa de las partes.",
        [],
    ),
    (
        "negativo",
        "El Tribunal Constitucional y el Banco de Espana han emitido informe "
        "favorable sobre la propuesta normativa.",
        [],
    ),
    (
        "negativo",
        "ANEXO II - RELACION DE DOCUMENTOS APORTADOS AL PROCEDIMIENTO",
        [],
    ),
    # ---------------- positivos: hay que tachar ----------------
    (
        "positivo",
        f"Comparece «Inigo Vallespin Arrondo», mayor de edad, provisto de DNI "
        f"«{DNI_1}», y manifiesta su voluntad de otorgar el presente poder.",
        ["NOMBRE", "DNI"],
    ),
    (
        "positivo",
        f"La parte demandada, «Xiomara Belmonte Quiroga», con NIE «{NIE_1}», "
        f"fue emplazada en forma legal.",
        ["NOMBRE", "NIE"],
    ),
    (
        "positivo",
        "El domicilio a efectos de notificaciones se fija en «calle Herreria de "
        "los Olmos 14, 3o B, 28912 Leganes».",
        ["DIRECCION"],
    ),
    (
        "positivo",
        f"El abono se realizara mediante transferencia a la cuenta «{IBAN_1}» "
        f"titularidad del arrendador.",
        ["IBAN"],
    ),
    (
        "positivo",
        f"Consta afiliacion a la Seguridad Social con numero «{SS_1}» desde el "
        f"inicio de la relacion laboral.",
        ["SEG_SOCIAL"],
    ),
    (
        "positivo",
        "Puede dirigir cualquier comunicacion a «zurine.olabarrieta@correo-ficticio.example» "
        "o al telefono «612 04 87 39».",
        ["EMAIL", "TELEFONO"],
    ),
    (
        "positivo",
        "La interesada, nacida el «14 de marzo de 1979», acredita residencia "
        "continuada en territorio nacional.",
        ["FECHA"],
    ),
    (
        "positivo",
        f"Actua en nombre propio «Teodulfo Manzanedo Iriarte», con DNI "
        f"«{DNI_2}», y en representacion de la entidad prestataria.",
        ["NOMBRE", "DNI"],
    ),
    (
        "positivo",
        "El letrado «Anselmo Trigueros Bermejo» y la procuradora "
        "«Ludivina Cabrejas Montiel» suscriben el presente escrito.",
        ["NOMBRE", "NOMBRE"],
    ),
    (
        "positivo",
        "Se cita a «Rosalinda Etxeberria Zugasti» para la vista senalada, "
        "domiciliada en «avenida del Molino Viejo 208, 41013 Sevilla».",
        ["NOMBRE", "DIRECCION"],
    ),
    # ---------------- trampas: apellidos que chocan con NEVER_IN_NAME ------
    (
        "trampa",
        "El compareciente «Nicomedes Banco Salcedo» acepta las condiciones "
        "economicas pactadas en el documento.",
        ["NOMBRE"],
    ),
    (
        "trampa",
        "Se persona en autos «Efigenia Construccion Belaustegui» en calidad de "
        "administradora concursal.",
        ["NOMBRE"],
    ),
    (
        "trampa",
        "La testigo «Maravillas Contrato Ibarrola» declara sobre los hechos "
        "objeto del procedimiento.",
        ["NOMBRE"],
    ),
    (
        "trampa",
        "Firma en prueba de conformidad «Baldomero Datos Urquiaga», sin que "
        "conste oposicion alguna.",
        ["NOMBRE"],
    ),
    # ---------------- trampas: nombre dentro de encabezado en mayusculas ---
    (
        "trampa",
        "DECLARACION DE «SATURNINO PELAYO GOIKOETXEA» ANTE EL SECRETARIO "
        "JUDICIAL",
        ["NOMBRE"],
    ),
    (
        "trampa",
        "COMPARECENCIA DE «HERMENEGILDA LOSTAU BIZKARRA» - ACTA NUMERO 77",
        ["NOMBRE"],
    ),
    # ---------------- trampas: toponimo usado como apellido ---------------
    (
        "trampa",
        "El fiador solidario «Casimiro Toledo Aranguren» responde de las "
        "obligaciones asumidas.",
        ["NOMBRE"],
    ),
    (
        "trampa",
        "Interviene asimismo «Purificacion Palencia Sarasola» como parte "
        "codemandada en el litigio.",
        ["NOMBRE"],
    ),
    # ---------------- trampas: negativo que se parece a nombre ------------
    (
        "trampa",
        "El Juzgado de lo Social numero 2 de Getafe dicto sentencia estimatoria "
        "en fecha reciente.",
        [],
    ),
    (
        "trampa",
        "Se aplica el Real Decreto Legislativo 2/2015 y el Estatuto de los "
        "Trabajadores en su redaccion vigente.",
        [],
    ),
]


def _parse(texto: str, etiquetas: list[str]) -> tuple[str, list[dict]]:
    """Strip « » markers and turn them into character offsets.

    Returns the clean text and the span list in ``GoldSpan.to_dict()`` form.
    Raises if the marker count and the label count disagree, which is the whole
    point of authoring the corpus this way.
    """
    limpio: list[str] = []
    spans: list[dict] = []
    resto = texto
    indice_etiqueta = 0
    while True:
        apertura = resto.find(OPEN)
        if apertura == -1:
            limpio.append(resto)
            break
        cierre = resto.find(CLOSE, apertura)
        if cierre == -1:
            raise ValueError(f"unclosed {OPEN} in: {texto[:60]!r}")
        limpio.append(resto[:apertura])
        inicio = sum(len(p) for p in limpio)
        valor = resto[apertura + 1 : cierre]
        limpio.append(valor)
        if indice_etiqueta >= len(etiquetas):
            raise ValueError(f"more markers than labels in: {texto[:60]!r}")
        spans.append(
            {
                "category": etiquetas[indice_etiqueta],
                "start": inicio,
                "end": inicio + len(valor),
            }
        )
        indice_etiqueta += 1
        resto = resto[cierre + 1 :]

    if indice_etiqueta != len(etiquetas):
        raise ValueError(f"more labels than markers in: {texto[:60]!r}")

    completo = "".join(limpio)
    for span in spans:
        fragmento = completo[span["start"] : span["end"]]
        if not fragmento:
            raise ValueError(f"empty span in: {texto[:60]!r}")
    return completo, spans


def build_records() -> list[dict]:
    """Return the corpus as gold-store JSONL records."""
    registros: list[dict] = []
    for grupo, texto, etiquetas in CORPUS:
        limpio, spans = _parse(texto, etiquetas)
        registros.append({"text": limpio, "label": spans, "grupo": grupo})
    return registros


def main() -> None:
    destino = Path(__file__).with_name("gold_sintetico.jsonl")
    registros = build_records()
    with open(destino, "w", encoding="utf-8") as fh:
        for registro in registros:
            fh.write(json.dumps(registro, ensure_ascii=False) + "\n")

    por_grupo: dict[str, int] = {}
    spans = 0
    for registro in registros:
        por_grupo[registro["grupo"]] = por_grupo.get(registro["grupo"], 0) + 1
        spans += len(registro["label"])
    print(f"wrote {destino}")
    print(f"examples: {len(registros)}  spans: {spans}")
    for grupo in sorted(por_grupo):
        print(f"  {grupo}: {por_grupo[grupo]}")


if __name__ == "__main__":
    main()
