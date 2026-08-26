"""Deterministic recognizers for Spanish identifiers.

Each recognizer combines a regex with a check-digit / structural validator
(DNI letter, CIF control, IBAN mod-97, Luhn, Seguridad Social mod-97) so the
match is confirmed before a span is emitted. This drives false-positive rates
close to zero for identifier types that carry their own control digit.

The public entry point is ``analyze(text) -> list[Recognition]``. The module
is intentionally standalone (pure Python, no NLP deps) so Phase 1 does not
require any model download. The ``Recognition`` shape and the recognizer
interface (``pattern``, ``validator``, ``context``, ``score``) mirror
Microsoft Presidio's ``RecognizerResult`` / ``PatternRecognizer`` so the
catalog can be adapted to a Presidio ``AnalyzerEngine`` in Phase 3 (ensemble
with a Spanish NER) without touching detections.

Entities produced:
    ES_DNI, ES_NIE, ES_NIF_CIF, ES_IBAN, ES_SSN,
    ES_PHONE, ES_POSTAL_CODE, ES_LICENSE_PLATE,
    ES_CADASTRAL_REF, ES_CREDIT_CARD
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Recognition:
    """One validated span produced by a recognizer."""

    start: int
    end: int
    entity_type: str
    score: float
    text: str


# --- Validators ------------------------------------------------------------

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIE_PREFIX = {"X": "0", "Y": "1", "Z": "2"}
_CIF_CONTROL_LETTERS = "JABCDEFGHI"
_CIF_ONLY_LETTER = frozenset("KPQRSNW")
_CIF_ONLY_DIGIT = frozenset("ABEH")


def validate_dni(text: str) -> bool:
    """Validate a Spanish DNI (8 digits + control letter)."""
    s = re.sub(r"[\s.-]", "", text).upper()
    if len(s) != 9 or not s[:8].isdigit():
        return False
    return _DNI_LETTERS[int(s[:8]) % 23] == s[8]


def validate_nie(text: str) -> bool:
    """Validate a Spanish NIE (X/Y/Z + 7 digits + control letter)."""
    s = re.sub(r"[\s.-]", "", text).upper()
    if len(s) != 9 or s[0] not in _NIE_PREFIX or not s[1:8].isdigit():
        return False
    return _DNI_LETTERS[int(_NIE_PREFIX[s[0]] + s[1:8]) % 23] == s[8]


def validate_cif(text: str) -> bool:
    """Validate a Spanish CIF (legal entity tax id)."""
    s = re.sub(r"[\s.-]", "", text).upper()
    if len(s) != 9 or s[0] not in "ABCDEFGHJKLMNPQRSUVW":
        return False
    digits = s[1:8]
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch)
        if i % 2 == 0:  # odd positions in 1-indexed → doubled + digit-summed
            d = n * 2
            total += (d // 10) + (d % 10)
        else:
            total += n
    control_digit = (10 - (total % 10)) % 10
    control_letter = _CIF_CONTROL_LETTERS[control_digit]
    control = s[8]
    if s[0] in _CIF_ONLY_DIGIT:
        return control == str(control_digit)
    if s[0] in _CIF_ONLY_LETTER:
        return control == control_letter
    return control == str(control_digit) or control == control_letter


def validate_iban(text: str) -> bool:
    """Validate any IBAN via the ISO 13616 mod-97 check."""
    s = re.sub(r"\s", "", text).upper()
    if not (15 <= len(s) <= 34) or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    rearranged = s[4:] + s[:4]
    numeric: list[str] = []
    for ch in rearranged:
        if ch.isdigit():
            numeric.append(ch)
        elif "A" <= ch <= "Z":
            numeric.append(str(ord(ch) - 55))
        else:
            return False
    try:
        return int("".join(numeric)) % 97 == 1
    except ValueError:
        return False


def validate_ss(text: str) -> bool:
    """Validate a Spanish Seguridad Social affiliation number (12 digits, mod-97)."""
    s = re.sub(r"[\s/.-]", "", text)
    if len(s) != 12 or not s.isdigit():
        return False
    return int(s[:10]) % 97 == int(s[10:])


def validate_luhn(text: str) -> bool:
    """Validate a payment-card PAN via the Luhn algorithm."""
    s = re.sub(r"[\s-]", "", text)
    if not (13 <= len(s) <= 19) or not s.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(s)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# --- Recognizer catalog ----------------------------------------------------

@dataclass(frozen=True)
class _Recognizer:
    entity_type: str
    pattern: re.Pattern
    score: float
    validator: Callable[[str], bool] | None
    context: tuple[str, ...] = ()
    # When True, ``context`` stops being a score bonus and becomes a
    # precondition: no context word nearby, no match. Needed for patterns whose
    # shape alone is too generic to stand on its own — a 20-26 character
    # alphanumeric run is a verification code, a cadastral reference or a hash,
    # and only the surrounding words tell them apart.
    require_context: bool = False


def _re(pat: str) -> re.Pattern:
    return re.compile(pat)


# --- Street-address building blocks ---------------------------------------
#
# A Spanish postal address is recognisable by shape, and leaving it to the
# statistical layers was losing it outright: measured on a real tax document,
# "Cl. Sant Ferran, 0176 de Sabadell" survived redaction in full, and the next
# address had only its street name removed — labelled NOMBRE, because "Frederic
# Soler" is a person's name used as a street name.
#
# TWO independent signals are always required, a street-type marker AND a house
# number or "s/n". A marker alone fires on "Cl@ve PIN" (the tax agency's login
# system) and on any sentence opening with "Plaza". Requiring the number is what
# makes this safe as a deterministic match, since deterministic spans bypass the
# false-positive filter — the privilege ES_PHONE holds on a bare regex, which is
# how PDF content-stream fragments ended up redacted as phone numbers.

# Case-insensitive: addresses are written "Cl." and "calle" alike. The street
# NAME must still be capitalised, which is what keeps "la calle es competencia
# municipal" and "Plaza vacante en el tribunal" from matching.
_STREET_MARKER = (
    r"\b(?i:C/|Cl|Cla|Calle|Carrer|Av|Avda|Avenida|Avinguda|Pza|Plza|Plaza|"
    r"Pla[çc]a|Ps|Ps[eo]|Paseo|Passeig|Ctra|Carretera|Rda|Ronda|Rbla|Rambla|"
    r"Trav|Travesia|Traves[ií]a|Cam[ií]|Camino|Gta|Glorieta|Urb|"
    r"Urbanizaci[oó]n|Pol[ií]gono|Pol)\.?"
)

# Connectors real street names use. They may appear several in a row ("de los
# Olmos") and may lead the name outright ("del Molino Viejo").
_CONNECTOR = r"(?:de|del|dels|de\s+la|de\s+los|de\s+las|la|las|los|les|el|i|y|d')"
_NAME_WORD = r"[A-ZÁÉÍÓÚÜÑÇ][\w'’·-]*"
_STREET_NAME = (
    r"(?:" + _CONNECTOR + r"\s+)*" + _NAME_WORD
    + r"(?:\s+(?:" + _CONNECTOR + r"\s+)*" + _NAME_WORD + r"){0,4}"
)

_HOUSE_NUMBER = r"(?:n[.ºo°]?\s*)?(?:\d{1,5}[ºª°]?|s/n)\b"

# Floor and door. A bare number is NOT accepted as a floor: with three
# repetitions allowed, "\\d{1,3}" swallowed a postcode in pieces ("082" + "02"),
# truncating "CL BOSCH, 1 08202 SABADELL" to "CL BOSCH, 1 08202". A floor must
# carry an ordinal marker — º, ª, ° or the bare "o"/"a" that PDF extraction
# leaves behind — or be a spelled-out level.
_FLOOR = r"(?:\d{1,3}\s*(?:[ºª°]|[oa]\b)|bajo|entlo|entresuelo|[Pp]iso|[Pp]ta|[Pp]uerta|[Ee]sc)"
# A lone capital letter is a door only when nothing alphabetic follows it;
# without that guard it ate the first letter of the municipality.
_DOOR = r"[A-Z](?![A-Za-zÁÉÍÓÚÜÑÇ])"
_FLOOR_DOOR = r"(?:\s*,?\s*(?:" + _FLOOR + r"|" + _DOOR + r")\.?){0,3}"

# After a postcode, a capitalised run is the municipality.
_POSTCODE_AND_CITY = (
    r"(?:\s*,?\s*\d{5}(?:\s+" + _NAME_WORD + r"(?:\s+" + _NAME_WORD + r"){0,2})?)?"
)
# "…, 0176 de Sabadell" — no postcode, the municipality hangs off "de".
_CITY_AFTER_DE = r"(?:\s+de\s+" + _NAME_WORD + r"(?:\s+" + _NAME_WORD + r"){0,2})?"


_RECOGNIZERS: tuple[_Recognizer, ...] = (
    _Recognizer(
        entity_type="ES_DNI",
        pattern=_re(r"\b\d{8}[-.\s]?[A-HJ-NP-TV-Z]\b"),
        score=0.9,
        validator=validate_dni,
        context=("dni", "documento", "identidad", "nif"),
    ),
    _Recognizer(
        entity_type="ES_NIE",
        pattern=_re(r"\b[XYZ][-.\s]?\d{7}[-.\s]?[A-HJ-NP-TV-Z]\b"),
        score=0.9,
        validator=validate_nie,
        context=("nie", "residente", "extranjero"),
    ),
    _Recognizer(
        entity_type="ES_NIF_CIF",
        pattern=_re(r"\b[ABCDEFGHJKLMNPQRSUVW]\d{7}[0-9A-J]\b"),
        score=0.9,
        validator=validate_cif,
        context=("cif", "nif", "sociedad", "empresa", "s.a", "s.l"),
    ),
    _Recognizer(
        entity_type="ES_IBAN",
        pattern=_re(r"\bES\d{2}(?:[\s-]?\d{4}){4}[\s-]?\d{4}\b"),
        score=0.9,
        validator=validate_iban,
        context=("iban", "cuenta", "bancaria", "bancario", "corriente"),
    ),
    _Recognizer(
        entity_type="ES_SSN",
        pattern=_re(r"\b\d{2}[\s/.-]?\d{8}[\s/.-]?\d{2}\b"),
        score=0.85,
        validator=validate_ss,
        context=("seguridad social", "afiliación", "afiliacion", "n.a.s.s"),
    ),
    _Recognizer(
        entity_type="ES_CREDIT_CARD",
        # 13-19 digits, optionally in groups of 4 with spaces or hyphens.
        pattern=_re(r"\b(?:\d{4}[\s-]){3}\d{1,4}\b|\b\d{13,19}\b"),
        score=0.85,
        validator=validate_luhn,
        context=("tarjeta", "visa", "mastercard", "crédito", "credito",
                 "débito", "debito", "amex"),
    ),
    _Recognizer(
        entity_type="ES_PHONE",
        # Optional +34/0034 prefix, then 6/7/8/9 + 8 digits, grouped freely.
        pattern=_re(r"(?:(?:\+|00)34[\s.-]?)?\b[6789]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}\b"),
        score=0.55,
        validator=None,
        context=("teléfono", "telefono", "móvil", "movil", "tel", "tfno"),
    ),
    _Recognizer(
        entity_type="ES_POSTAL_CODE",
        # 5 digits, first two in 01..52 (Spanish province codes).
        pattern=_re(r"\b(?:0[1-9]|[1-4]\d|5[0-2])\d{3}\b"),
        score=0.45,
        validator=None,
        context=("cp", "código postal", "codigo postal", "c.p."),
    ),
    _Recognizer(
        entity_type="ES_LICENSE_PLATE",
        # New format NNNN LLL (post-2000, no vowels) or old L(L)-NNNN-LL.
        pattern=_re(
            r"\b\d{4}[\s-]?[BCDFGHJKLMNPRSTVWXYZ]{3}\b"
            r"|\b[A-Z]{1,2}[\s-]?\d{4}[\s-]?[A-Z]{1,2}\b"
        ),
        score=0.5,
        validator=None,
        context=("matrícula", "matricula", "vehículo", "vehiculo"),
    ),
    _Recognizer(
        entity_type="ES_CADASTRAL_REF",
        # 20-char alphanumeric reference (structural check only).
        pattern=_re(r"\b\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2}\b"),
        score=0.9,
        validator=None,
        context=("catastro", "catastral", "referencia catastral"),
    ),
    _Recognizer(
        entity_type="ES_REGISTRY_REF",
        # Land/company registry locators: "Tomo 4097", "Libro 916",
        # "Folio 64", "Hoja 286", "Inscripción 4", "Finca Registral 817".
        #
        # These need their own deterministic recognizer precisely *because*
        # "tomo"/"libro"/"folio"/"hoja" live in the boilerplate lexicon used to
        # reject NER noise — without this, the filter would drop them, which
        # contradicts the policy of redacting every registry identifier.
        # Deterministic matches bypass that filter.
        pattern=_re(
            r"\b(?:[Tt]omo|[Ll]ibro|[Ll]libre|[Ff]olio|[Ff]oli|[Hh]oja|"
            r"[Ii]nscripci[oó]n|[Ii]nscripci[oó]|[Ff]inca(?:\s+[Rr]egistral)?|"
            r"[Ss]ecci[oó]n)\s*n?[.ºo°]?\s*\d{1,6}\b"
        ),
        score=0.85,
        validator=None,
        context=("registro", "registral", "propiedad", "mercantil",
                 "inscripción", "inscripcion", "finca"),
    ),
    _Recognizer(
        entity_type="ES_VERIFICATION_CODE",
        # Código Seguro de Verificación: the code that lets anyone fetch the
        # original document from the issuing body's website. It is not another
        # identifier, it is a CREDENTIAL — an "anonymised" copy carrying its own
        # CSV can be traded back for the original, so the anonymisation is
        # reversible by whoever receives it. Under art. 4(5) GDPR the additional
        # information needed to re-identify has to be kept separately; here it
        # travels inside the document.
        #
        # Shape alone cannot carry this: a 20-26 character alphanumeric run is
        # also a cadastral reference or a hash. Hence require_context — the only
        # recognizer that uses it — so a match needs one of the words below
        # nearby. Without that the pattern fired on every cadastral reference in
        # the document.
        pattern=_re(r"\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{16,32}\b"),
        score=0.8,
        validator=None,
        require_context=True,
        context=(
            "código seguro de verificación", "codigo seguro de verificacion",
            "csv", "c.s.v", "autenticidad", "verificable", "cotejo",
            "sede electrónica", "sede electronica", "verificación de",
            "verificacion de", "huella digital", "código de verificación",
            "codigo de verificacion",
        ),
    ),
    _Recognizer(
        entity_type="ES_STREET_ADDRESS",
        # Spanish postal addresses have a recognisable shape, and leaving them
        # to the statistical layers was losing them outright: measured on a real
        # tax document, "Cl. Sant Ferran, 0176 de Sabadell" survived redaction
        # in full, while the next address had only its street name removed — and
        # labelled NOMBRE, because "Frederic Soler" is a person's name used as a
        # street name.
        #
        # TWO independent signals are required, a street-type marker AND a house
        # number or "s/n". A marker alone fires on "Cl@ve PIN", the tax agency's
        # login system, and on any sentence starting with "Plaza". Requiring the
        # number is what makes this safe enough to be a deterministic match,
        # since deterministic spans bypass the false-positive filter — the very
        # privilege ES_PHONE holds on a bare regex, which is how PDF
        # content-stream fragments ended up redacted as phone numbers.
        pattern=_re(
            # Built from named pieces rather than one long expression: every
            # part below was added because a real address failed without it.
            _STREET_MARKER          # "Cl.", "calle", "avenida" — any case
            # Punctuation may sit between marker and name: PDF extraction
            # produced "Cl., Frederic Soler" in one of three occurrences of the
            # same address, and requiring plain whitespace lost exactly that one.
            + r"[\s,]+"
            + _STREET_NAME          # "Sant Ferran", "del Molino Viejo",
                                    # "Herreria de los Olmos"
            + r"[,\s]+" + _HOUSE_NUMBER   # second signal: "14", "s/n", "nº 3"
            + _FLOOR_DOOR           # optional "3º B", "bajo", "esc. 2"
            + _POSTCODE_AND_CITY    # optional "28912 Leganes"
            + _CITY_AFTER_DE        # optional "de Sabadell"
        ),
        # Below the check-digit entities (0.85-0.9): a shape match is strong
        # evidence but not proof. Above ES_POSTAL_CODE (0.45) and ES_PHONE
        # (0.55), so a full address wins the overlap against the bare postal
        # code inside it and against a NOMBRE span over the street name.
        score=0.7,
        validator=None,
        context=("domicilio", "direccion", "dirección", "sito", "sita",
                 "situado", "situada", "vivienda", "inmueble", "finca",
                 "notificaciones", "residencia"),
    ),
)


_CONTEXT_WINDOW = 60
_CONTEXT_BOOST = 0.15


def _has_context(text: str, start: int, end: int, ctx: tuple[str, ...]) -> bool:
    """True when any context word appears within the window around the match."""
    if not ctx:
        return False
    lo = max(0, start - _CONTEXT_WINDOW)
    hi = min(len(text), end + _CONTEXT_WINDOW)
    window = text[lo:hi].lower()
    return any(word in window for word in ctx)


def _context_boost(text: str, start: int, end: int, ctx: tuple[str, ...]) -> float:
    return _CONTEXT_BOOST if _has_context(text, start, end, ctx) else 0.0


def analyze(text: str) -> list[Recognition]:
    """Run every recognizer over ``text``; return only structure-validated hits."""
    if not text:
        return []
    hits: list[Recognition] = []
    for rec in _RECOGNIZERS:
        for m in rec.pattern.finditer(text):
            matched = m.group()
            if rec.validator is not None and not rec.validator(matched):
                continue
            if rec.require_context and not _has_context(
                text, m.start(), m.end(), rec.context
            ):
                continue
            score = min(1.0, rec.score + _context_boost(text, m.start(), m.end(), rec.context))
            hits.append(Recognition(
                start=m.start(),
                end=m.end(),
                entity_type=rec.entity_type,
                score=score,
                text=matched,
            ))
    return hits + _propagate_context_confirmed(text, hits)


_CONTEXT_REQUIRED_TYPES = frozenset(
    r.entity_type for r in _RECOGNIZERS if r.require_context
)


def _propagate_context_confirmed(
    text: str, hits: list[Recognition],
) -> list[Recognition]:
    """Extend a context-confirmed value to its other occurrences in the text.

    ``require_context`` asks for explanatory words near the match, which the
    first mention of a verification code has and later ones do not: measured on
    a real tax document, the code appeared twice — once under "Código Seguro de
    Verificación" and once on the signature page beside "Fdo.:______" — and only
    the first was caught, so the credential still shipped.

    Widening the context window would trade the precision the requirement buys.
    Identity is the sounder argument: the same string in the same document is the
    same code, so one confirmed occurrence licenses the rest.
    """
    if not _CONTEXT_REQUIRED_TYPES:
        return []
    confirmed = {
        (h.entity_type, h.text, h.score)
        for h in hits
        if h.entity_type in _CONTEXT_REQUIRED_TYPES
    }
    if not confirmed:
        return []
    taken = {(h.start, h.end) for h in hits}
    extra: list[Recognition] = []
    for entity_type, value, score in confirmed:
        for m in re.finditer(re.escape(value), text):
            if (m.start(), m.end()) in taken:
                continue
            taken.add((m.start(), m.end()))
            extra.append(Recognition(
                start=m.start(),
                end=m.end(),
                entity_type=entity_type,
                score=score,
                text=value,
            ))
    return extra
