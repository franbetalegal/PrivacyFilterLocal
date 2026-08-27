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

- ``negative``: text that must NOT be redacted (legal boilerplate, court and
  institution names, statute citations, all-caps headings, amounts, company
  names, case-file references, bare toponyms).
- ``positive``: text that MUST be redacted (invented people in legal context,
  full addresses, valid-checksum identifiers, birth dates, contact details).
- ``trap``: the interesting group. Surnames that collide with entries in
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

    .venv/bin/python tests/fixtures/generate_synthetic_gold.py

which rewrites ``synthetic_gold.jsonl`` next to this file.
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


def dni(number: int) -> str:
    """Build a syntactically valid DNI from an 8-digit number."""
    return f"{number:08d}{_DNI_LETTERS[number % 23]}"


def nie(prefix: str, number: int) -> str:
    """Build a syntactically valid NIE (X/Y/Z + 7 digits + letter)."""
    base = int(f"{'XYZ'.index(prefix)}{number:07d}")
    return f"{prefix}{number:07d}{_DNI_LETTERS[base % 23]}"


def spanish_iban(bank_code: str, branch: str, check_digits: str, account: str) -> str:
    """Build a valid Spanish IBAN, computing the ISO 13616 mod-97 check."""
    bban = f"{bank_code}{branch}{check_digits}{account}"
    # ES00 placeholder moved to the end, letters mapped to numbers (E=14, S=28).
    rearranged = f"{bban}142800"
    check = 98 - (int(rearranged) % 97)
    return f"ES{check:02d}{bban}"


def social_security(province: str, number: str) -> str:
    """Build a valid Seguridad Social number with its mod-97 control digits."""
    base = f"{province}{number}"
    control = int(base) % 97
    return f"{base}{control:02d}"


# Identifiers are generated, then verified with the project's own validators so
# a broken generator cannot silently poison the corpus.
DNI_1 = dni(41_820_573)
DNI_2 = dni(7_493_162)
NIE_1 = nie("Y", 4_812_907)
IBAN_1 = spanish_iban("2085", "8371", "44", "0301827465")
SS_1 = social_security("28", "1049783625"[:8])

for _value, _validator, _name in (
    (DNI_1, validate_dni, "DNI_1"),
    (DNI_2, validate_dni, "DNI_2"),
    (NIE_1, validate_nie, "NIE_1"),
    (IBAN_1, validate_iban, "IBAN_1"),
    (SS_1, validate_ss, "SS_1"),
):
    if not _validator(_value):
        raise AssertionError(f"{_name}={_value!r} rejected by {_validator.__name__}")


# --- the corpus -----------------------------------------------------------
#
# Each entry: (group, texto_con_marcadores, [etiqueta por marcador en orden]).
# A negative example simply carries no markers and an empty label list.

CORPUS: list[tuple[str, str, list[str]]] = [
    # ---------------- negativos: nada que tachar ----------------
    (
        "negative",
        "El presente contrato se rige por lo dispuesto en el articulo 1256 del "
        "Codigo Civil y en la Ley 3/2004, de 29 de diciembre, de lucha contra "
        "la morosidad en las operaciones comerciales.",
        [],
    ),
    (
        "negative",
        "Visto el expediente por el Juzgado de Primera Instancia number 4 de "
        "Alcala de Henares, y oido el Ministerio Fiscal, procede acordar lo "
        "siguiente.",
        [],
    ),
    (
        "negative",
        "CLAUSULA SEXTA. OBLIGACIONES DE LAS PARTES",
        [],
    ),
    (
        "negative",
        "El importe total asciende a 14.750,80 euros, IVA del 21 % incluido, "
        "con un recargo del 5 % por demora superior a treinta dias naturales.",
        [],
    ),
    (
        "negative",
        "La entidad Suministros Industriales Anagrama S.L.U. figura inscrita en "
        "el Registro Mercantil con el number de asiento 118.409.",
        [],
    ),
    (
        "negative",
        "Notifiquese la presente resolucion a las partes y librese testimonio "
        "para su union a los autos, certificando lo actuado.",
        [],
    ),
    (
        "negative",
        "Procedimiento ordinario 512/2023, pieza separada de medidas cautelares, "
        "tramitada conforme al articulo 730 de la Ley de Enjuiciamiento Civil.",
        [],
    ),
    (
        "negative",
        "La competencia territorial corresponde a los tribunales de Barcelona "
        "por remision expresa de las partes.",
        [],
    ),
    (
        "negative",
        "El Tribunal Constitucional y el Banco de Espana han emitido informe "
        "favorable sobre la propuesta normativa.",
        [],
    ),
    (
        "negative",
        "ANEXO II - RELACION DE DOCUMENTOS APORTADOS AL PROCEDIMIENTO",
        [],
    ),
    # ---------------- positivos: hay que tachar ----------------
    (
        "positive",
        f"Comparece «Inigo Vallespin Arrondo», mayor de edad, provisto de DNI "
        f"«{DNI_1}», y manifiesta su voluntad de otorgar el presente poder.",
        ["NOMBRE", "DNI"],
    ),
    (
        "positive",
        f"La parte demandada, «Xiomara Belmonte Quiroga», con NIE «{NIE_1}», "
        f"fue emplazada en forma legal.",
        ["NOMBRE", "NIE"],
    ),
    (
        "positive",
        "El domicilio a efectos de notificaciones se fija en «calle Herreria de "
        "los Olmos 14, 3o B, 28912 Leganes».",
        ["DIRECCION"],
    ),
    (
        "positive",
        f"El abono se realizara mediante transferencia a la account «{IBAN_1}» "
        f"titularidad del arrendador.",
        ["IBAN"],
    ),
    (
        "positive",
        f"Consta afiliacion a la Seguridad Social con number «{SS_1}» desde el "
        f"start de la relacion laboral.",
        ["SEG_SOCIAL"],
    ),
    (
        "positive",
        "Puede dirigir cualquier comunicacion a «zurine.olabarrieta@correo-ficticio.example» "
        "o al telefono «612 04 87 39».",
        ["EMAIL", "TELEFONO"],
    ),
    (
        "positive",
        "La interesada, nacida el «14 de marzo de 1979», acredita residencia "
        "continuada en territorio nacional.",
        ["FECHA"],
    ),
    (
        "positive",
        f"Actua en nombre propio «Teodulfo Manzanedo Iriarte», con DNI "
        f"«{DNI_2}», y en representacion de la entidad prestataria.",
        ["NOMBRE", "DNI"],
    ),
    (
        "positive",
        "El letrado «Anselmo Trigueros Bermejo» y la procuradora "
        "«Ludivina Cabrejas Montiel» suscriben el presente escrito.",
        ["NOMBRE", "NOMBRE"],
    ),
    (
        "positive",
        "Se cita a «Rosalinda Etxeberria Zugasti» para la vista senalada, "
        "domiciliada en «avenida del Molino Viejo 208, 41013 Sevilla».",
        ["NOMBRE", "DIRECCION"],
    ),
    # ---------------- trampas: apellidos que chocan con NEVER_IN_NAME ------
    (
        "trap",
        "El compareciente «Nicomedes Banco Salcedo» acepta las condiciones "
        "economicas pactadas en el documento.",
        ["NOMBRE"],
    ),
    (
        "trap",
        "Se persona en autos «Efigenia Construccion Belaustegui» en calidad de "
        "administradora concursal.",
        ["NOMBRE"],
    ),
    (
        "trap",
        "La testigo «Maravillas Contrato Ibarrola» declara sobre los hechos "
        "objeto del procedimiento.",
        ["NOMBRE"],
    ),
    (
        "trap",
        "Firma en prueba de conformidad «Baldomero Datos Urquiaga», sin que "
        "conste oposicion alguna.",
        ["NOMBRE"],
    ),
    # ---------------- trampas: nombre dentro de encabezado en mayusculas ---
    (
        "trap",
        "DECLARACION DE «SATURNINO PELAYO GOIKOETXEA» ANTE EL SECRETARIO "
        "JUDICIAL",
        ["NOMBRE"],
    ),
    (
        "trap",
        "COMPARECENCIA DE «GARAZI ELIZONDO ZUBIZARRETA» - ACTA NUMERO 12",
        ["NOMBRE"],
    ),
    # ---------------- trampas: toponimo usado como apellido ---------------
    (
        "trap",
        "El fiador solidario «Casimiro Toledo Aranguren» responde de las "
        "obligaciones asumidas.",
        ["NOMBRE"],
    ),
    (
        "trap",
        "Interviene asimismo «Purificacion Palencia Sarasola» como parte "
        "codemandada en el litigio.",
        ["NOMBRE"],
    ),
    # ---------------- trampas: negative que se parece a nombre ------------
    (
        "trap",
        "El Juzgado de lo Social number 2 de Getafe dicto sentencia estimatoria "
        "en fecha reciente.",
        [],
    ),
    (
        "trap",
        "Se aplica el Real Decreto Legislativo 2/2015 y el Estatuto de los "
        "Trabajadores en su redaccion vigente.",
        [],
    ),
    # ---------------- form: saltos de linea dentro de las entidades --
    #
    # Everything above is a single clean line, which is exactly what made this
    # corpus blind to the biggest real-world failure. PDF text extraction
    # hard-wraps lines and puts form fields on their own line, so entities are
    # cut in half and unrelated fields end up adjacent. Measured on a real
    # 28-page tax document: the taxpayer's name sat in a page header between an
    # identifier line and a field label, and survived redaction in 10 of its 11
    # occurrences, because the analyzer merged the three lines into one span and
    # then rejected it.
    (
        "form",
        "Ejercicio 2023. DATOS DECLARADOS POR EL CONTRIBUYENTE\n"
        f"«{DNI_1}»\n"
        "«ZUBELDIA MARTIARENA COVADONGA»\n"
        "Individual\nBienes inmuebles",
        ["DNI", "NOMBRE"],
    ),
    (
        "form",
        "Agencia Tributaria\nDelegacion Especial de CATALUNA\n"
        f"«{NIE_1}»\n"
        "«ARGUIÑANO BEITIALARRANGOITIA NEKANE»\n"
        "Autenticidad verificable",
        ["NIE", "NOMBRE"],
    ),
    (
        "form",
        # A name hard-wrapped mid-entity: the line break falls between the two
        # halves of the surname.
        "Vivienda situada en «Cl. Melchor \nBerrondo, 14» de Errenteria y "
        "terreno urbano en «Cl. Anastasio \nZugadi s/n» de Errenteria.",
        ["DIRECCION", "DIRECCION"],
    ),
    (
        "form",
        "El compareciente «Anselmo Iparraguirre \nZabaleta» ratifica el "
        "contenido del acta.",
        ["NOMBRE"],
    ),
    (
        "form",
        # Adjacent unrelated fields: the label must not be swept into the name,
        # and the amount on the next line must not be redacted.
        "Titular\n«Bittori Etxaniz Larrañaga»\nImporte declarado\n14.750,80 euros",
        ["NOMBRE"],
    ),
]


def _parse(text: str, labels: list[str]) -> tuple[str, list[dict]]:
    """Strip « » markers and turn them into character offsets.

    Returns the clean text and the span list in ``GoldSpan.to_dict()`` form.
    Raises if the marker count and the label count disagree, which is the whole
    point of authoring the corpus this way.
    """
    clean: list[str] = []
    spans: list[dict] = []
    rest = text
    label_index = 0
    while True:
        opening = rest.find(OPEN)
        if opening == -1:
            clean.append(rest)
            break
        closing = rest.find(CLOSE, opening)
        if closing == -1:
            raise ValueError(f"unclosed {OPEN} in: {text[:60]!r}")
        clean.append(rest[:opening])
        start = sum(len(p) for p in clean)
        value = rest[opening + 1 : closing]
        clean.append(value)
        if label_index >= len(labels):
            raise ValueError(f"more markers than labels in: {text[:60]!r}")
        spans.append(
            {
                "category": labels[label_index],
                "start": start,
                "end": start + len(value),
            }
        )
        label_index += 1
        rest = rest[closing + 1 :]

    if label_index != len(labels):
        raise ValueError(f"more labels than markers in: {text[:60]!r}")

    full = "".join(clean)
    for span in spans:
        fragment = full[span["start"] : span["end"]]
        if not fragment:
            raise ValueError(f"empty span in: {text[:60]!r}")
    return full, spans


def build_records() -> list[dict]:
    """Return the corpus as gold-store JSONL records."""
    records: list[dict] = []
    for group, text, labels in CORPUS:
        clean, spans = _parse(text, labels)
        records.append({"text": clean, "label": spans, "group": group})
    return records


def main() -> None:
    target = Path(__file__).with_name("synthetic_gold.jsonl")
    records = build_records()
    with open(target, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    by_group: dict[str, int] = {}
    spans = 0
    for record in records:
        by_group[record["group"]] = by_group.get(record["group"], 0) + 1
        spans += len(record["label"])
    print(f"wrote {target}")
    print(f"examples: {len(records)}  spans: {spans}")
    for group in sorted(by_group):
        print(f"  {group}: {by_group[group]}")


if __name__ == "__main__":
    main()
