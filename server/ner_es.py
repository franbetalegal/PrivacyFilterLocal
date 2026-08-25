"""Statistical Iberian NER via spaCy — an added recall layer on top of opf.

The opf model is documented as "primarily English" (see its own model card at
``privacy-filter/README.md``), so it under-detects Iberian person names,
locations and organisations in the kind of long, formal text that legal writs
produce. This module runs one or more spaCy pipelines (Spanish + Catalan by
default) in parallel and produces spans for PER/LOC/ORG that the merger in
:mod:`server.pipeline` fuses with the opf and deterministic detections.

Why multiple models
    Running the Spanish model on a Catalan document mistags common Catalan
    words as person/location names (they're out-of-vocabulary), producing
    over-redaction of body text. Running both models and merging their
    detections is more accurate than picking one: the overlap resolver in the
    pipeline drops duplicates. A model that is not installed is silently
    skipped, so shipping only ``es_core_news_lg`` remains a valid setup.

Why spaCy directly instead of Microsoft Presidio's ``SpacyRecognizer``
    The pipeline already owns a well-tested overlap resolver and consistent
    pseudonymizer (see :mod:`server.pipeline`). Wrapping spaCy through Presidio
    would duplicate merge/anonymization logic. We keep spaCy as a bare NER
    layer and let our own merger arbitrate priorities.

The spaCy models are loaded lazily and cached; if none of the configured
models can be loaded the module logs a one-time warning and returns an empty
list, so the app degrades gracefully to opf + deterministic recognizers.

Language routing
    When more than one model is loaded, the analyzer splits the input into
    paragraph blocks and detects the language of each one independently (via
    ``langdetect``), then runs only the model that matches on that block. This
    handles mixed-language legal documents (Spanish header + Catalan body,
    bilingual sections, etc.) without letting one language's model tag the
    other language's common words as person names. For short blocks below
    detection threshold, or when detection fails, every loaded model runs on
    that block and results are unioned — safer to slightly over-detect than
    to lose coverage.

Configuration via environment variables:
    - ``PF_NER_MODELS``  Comma-separated model names to load in order
      (default: ``es_core_news_lg,ca_core_news_lg``).
    - ``PF_NER_MODEL``   Legacy single-model alias; used only when
      ``PF_NER_MODELS`` is unset.
    - ``PF_NER_LABELS``  Comma-separated entity types to keep (default:
      ``PER,LOC``). Add ``ORG`` to redact company and institution names, or
      ``MISC`` if the domain benefits from it.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from server.lexicon_es import (
    ENTITY_MARKERS,
    GENERIC_SINGLE,
    MONTHS,
    NEVER_IN_NAME,
    NUMBER_WORDS,
    PERSON_TRIGGER_PHRASES,
    PERSON_TRIGGER_PHRASES_LEADING,
    PERSON_TRIGGER_WORDS,
    PUBLIC_PHRASES,
    REGIONS,
    STREET_MARKERS,
    VERBS,
)

logger = logging.getLogger("privacy_filter.ner_es")

# Model list resolution: PF_NER_MODELS wins; fall back to legacy PF_NER_MODEL;
# fall back to the built-in default (Spanish + Catalan).
_MODELS_ENV = os.environ.get("PF_NER_MODELS")
_LEGACY_MODEL = os.environ.get("PF_NER_MODEL")
if _MODELS_ENV:
    _MODEL_NAMES = tuple(m.strip() for m in _MODELS_ENV.split(",") if m.strip())
elif _LEGACY_MODEL:
    _MODEL_NAMES = (_LEGACY_MODEL.strip(),)
else:
    _MODEL_NAMES = ("es_core_news_lg", "ca_core_news_lg")

# ORG is off by default. Policy: a legal-entity name is what keeps a document
# readable ("Agencia Tributaria", the bank, the counterparty), and redacting it
# rarely protects a natural person — the entity is identified by its NIF
# anyway. Measured on a real 28-page tax document, ORG accounted for 51 of 154
# redactions (33%), which is what made the output unusable. Set
# PF_NER_LABELS=PER,LOC,ORG to restore the previous behaviour.
_ENABLED_LABELS = frozenset(
    label.strip() for label in os.environ.get("PF_NER_LABELS", "PER,LOC").split(",")
    if label.strip()
)

# Score below opf (0.85) so opf-preferred boundaries win on overlap ties, above
# the unvalidated deterministic entities (e.g. ES_POSTAL_CODE=0.45) so a
# well-identified name doesn't lose to a generic 5-digit match on tied offsets.
_NER_BASELINE_SCORE = 0.7

_nlps: list = []
_nlps_by_lang: dict[str, list] = {}
_load_lock = threading.Lock()
_loaded = False


# Map spaCy model prefixes to the ISO code langdetect returns.
_MODEL_LANG_PREFIX = (
    ("es_", "es"),
    ("ca_", "ca"),
    ("pt_", "pt"),
    ("fr_", "fr"),
    ("it_", "it"),
    ("en_", "en"),
    ("de_", "de"),
)


def _lang_of(model_name: str) -> str | None:
    for prefix, lang in _MODEL_LANG_PREFIX:
        if model_name.startswith(prefix):
            return lang
    return None


@dataclass(frozen=True)
class NERSpan:
    """One PER/LOC/ORG match produced by spaCy."""

    start: int
    end: int
    entity_type: str
    score: float
    text: str


def _load() -> None:
    """Load and cache every configured spaCy pipeline. No-op after first call."""
    global _loaded
    if _loaded:
        return
    with _load_lock:
        if _loaded:
            return
        try:
            import spacy
        except ImportError as exc:
            logger.warning(
                "NER disabled: %s. Install with 'pip install spacy'.", exc,
            )
            _loaded = True
            return
        for name in _MODEL_NAMES:
            try:
                nlp = spacy.load(
                    name,
                    disable=["parser", "attribute_ruler", "lemmatizer"],
                )
            except OSError as exc:
                logger.warning(
                    "NER model %r not installed (%s). "
                    "Install with 'python -m spacy download %s'.",
                    name, exc, name,
                )
                continue
            _nlps.append(nlp)
            lang = _lang_of(name)
            if lang:
                _nlps_by_lang.setdefault(lang, []).append(nlp)
            logger.info("Loaded NER model: %s (lang=%s)", name, lang or "?")
        if not _nlps:
            logger.warning(
                "No NER models available; the app will run on opf + "
                "deterministic recognizers only.",
            )
        _loaded = True


def is_available() -> bool:
    """Return True when the NER layer will actually contribute spans."""
    _load()
    return bool(_nlps)


_MIN_BLOCK_LEN_FOR_DETECT = 40
_PARAGRAPH_SEP = re.compile(r"\n\s*\n")


# Kept as a module-level alias so existing tests and callers that referenced
# the old private name keep working.
_SINGLE_TOKEN_STOPLIST = GENERIC_SINGLE | NEVER_IN_NAME | VERBS

_HEADING_ALLCAPS = re.compile(r"^[A-ZÁÉÍÓÚÜÑÇ][A-ZÁÉÍÓÚÜÑÇ0-9]{2,}$")

# Prepositions / articles that never start a real proper-noun run in ES/CA.
_LEADING_STOPWORDS = frozenset({
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "al", "a", "en", "por", "para", "con", "sin", "entre", "sobre",
    "hacia", "desde", "hasta", "que", "como", "si", "no", "y", "o", "u",
    "dels", "les", "els", "amb", "per", "sense", "sota", "davant",
    "darrere", "cap", "fins", "des", "com", "e",
})

# Function words that dominate a heading fragment but are rare inside person
# names. Real names may contain "de la" (Juan de la Cruz) but never "EN", "SE".
_FUNCTION_WORDS = _LEADING_STOPWORDS | frozenset({
    "se", "es", "lo", "su", "sus", "le", "les", "me", "te", "nos", "ha",
    "han", "he", "hay", "sea", "sean", "ser", "son", "era", "fue", "muy",
    "mas", "más", "pero", "aunque", "cuando", "donde", "cual", "cuales",
    "quien", "quienes", "cuyo", "cuya", "esta", "este", "esto", "estas",
    "estos", "ese", "esa", "eso", "aquel", "todo", "toda", "todos", "todas",
    "otro", "otra", "otros", "otras", "mismo", "misma", "tal", "tales",
    "ho", "seu", "seus", "seva", "aquest", "aquesta", "aquell", "tot",
    "altre", "altres", "mateix",
})

# Trailing punctuation that turns a span into a form label, not an entity
# ("Codi Segur de Verificació:", "Datos personales =").
_LABEL_TRAILERS = (":", "=")

_PUBLIC_ENTITY_PHRASES = PUBLIC_PHRASES

# File-name extensions the NER sometimes wraps into ORG/PER (annex names).
_FILENAME_RE = re.compile(
    r".+\.(pdf|docx?|xlsx?|pptx?|txt|csv|jpe?g|png|tiff?|rtf|odt|ods|odp)$",
    re.IGNORECASE,
)

# OCR garbage: a long-ish run with no lowercase letters but mixed digits.
_OCR_GARBAGE_MIN_LEN = 15

# camelCase inside one token ("AasongoeRD", "MeaRODeRLY") — a lowercase letter
# immediately followed by an uppercase one. Real Spanish/Catalan words and
# names never do this; OCR of stylised text routinely does.
_INNER_CAPS_RE = re.compile(r"[a-záéíóúüñç][A-ZÁÉÍÓÚÜÑÇ]")

# A monetary amount / decimal measure: "790.794,11", "3,71MM", "262.185,85 €".
_AMOUNT_RE = re.compile(r"^[\d.,]*\d[\d.,]*\s*(€|eur|euros?|mm|m2|m²|ha|km|%)?$",
                        re.IGNORECASE)

# Law citation: "844/2016", "10/2010", "93/2017 de 16 de febrero", "38/2003".
# Requires a 4-digit year and exactly one slash, so a real calendar date
# ("31/10/2025", two slashes) is never mistaken for a statute reference.
_LAW_CITATION_RE = re.compile(r"^\d{1,4}\s*/\s*\d{4}\b")

# Legal-code abbreviations that mark a citation rather than an entity.
_LEGAL_CODES = frozenset({
    "lec", "lecrim", "lopj", "lsc", "lgt", "lo", "rd", "rdl", "cc", "cp",
    "ce", "et", "rgpd", "lssi", "lpac", "lrjsp", "trlgdcu", "ltaip",
    "cnae", "iae", "bis", "ter", "quater",
})

# Characters that only reach a span through OCR corruption or fragment
# concatenation — never inside a Spanish/Catalan name.
_STRUCTURAL_CHARS = "()[]{}~|©«»#*_\\"


def _normalized_phrase(text: str) -> str:
    """Lowercase and collapse whitespace/punctuation for phrase lookup."""
    cleaned = re.sub(r"\s+", " ", text.strip().strip(".,;:()[]{}\"'"))
    return cleaned.lower()


def _tokens_of(text: str) -> list[str]:
    """Whitespace tokens with surrounding punctuation stripped."""
    return [t.strip(".,;:()[]\"'·-–—") for t in text.split() if t.strip(".,;:()[]\"'·-–—")]


def _looks_like_ocr_garbage(text: str) -> bool:
    """True when the span is almost certainly mangled OCR output.

    Three independent signals, any of which is decisive:

    1. A long alphanumeric run with no lowercase at all (hashes, CSV codes).
    2. Mostly 1-2 character tokens — "Bs il ll il i ie", "Ç EI", "SD dat".
    3. camelCase inside a token — "AasongoeRD", "MeaRODeRLY".
    """
    stripped = text.strip()
    if not stripped:
        return True

    if (
        len(stripped) >= _OCR_GARBAGE_MIN_LEN
        and not any(c.islower() for c in stripped)
        and any(c.isdigit() for c in stripped)
        and any(c.isalpha() for c in stripped)
    ):
        return True

    tokens = _tokens_of(stripped)
    if len(tokens) >= 2:
        # Only count *meaningless* short tokens. Spanish function words ("de",
        # "la") and bare numbers are legitimately short and appear inside real
        # addresses ("Paseo de Pereda 9"), so excluding them keeps this rule
        # aimed at actual OCR debris ("Bs il ll il i ie", "Ç EI").
        tiny = sum(
            1 for t in tokens
            if len(t) <= 2 and t.lower() not in _FUNCTION_WORDS and not t.isdigit()
        )
        if tiny * 2 >= len(tokens):
            return True

    return any(_INNER_CAPS_RE.search(t) for t in tokens)


def _is_amount_or_measure(text: str) -> bool:
    """True for monetary amounts and measurements — never personal data."""
    stripped = text.strip().rstrip(".,;:-")
    if "€" in stripped or "%" in stripped:
        return True
    if not _AMOUNT_RE.match(stripped):
        return False
    # Require a decimal comma or a thousands dot so bare integers (which may
    # be an account or phone fragment) don't get swept up here.
    return "," in stripped or "." in stripped


_ARTICLE_REF_RE = re.compile(r"^(art|arts|artículo|articulo|article)\.?\s*\d",
                             re.IGNORECASE)


def _is_law_citation(text: str) -> bool:
    """True for law/article references: '844/2016', 'Art.4', '753.1 LEC'."""
    stripped = text.strip()
    # Two or more slashes means a calendar date (31/10/2025), not a statute.
    if stripped.count("/") == 1 and _LAW_CITATION_RE.match(stripped):
        return True
    if _ARTICLE_REF_RE.match(stripped):
        return True
    # Split on periods too so "Art.4" and "art.4 LEC" both surface "art".
    parts = {p.lower() for t in _tokens_of(stripped) for p in t.split(".") if p}
    return bool(parts & _LEGAL_CODES)


def _is_numeral_phrase(text: str) -> bool:
    """True when every meaningful token is a written-out number or a month.

    Catches deed headings ("NÚMERO SIETE", "CIENTO NOVENTA", "CINCO AÑOS",
    "VEINTISIETE DE ENERO DE") and bare month/ordinal fragments.
    """
    # Split hyphens as well so "Uno-cinco" and "Treinta-y-dos" are seen as
    # numerals. Real hyphenated surnames ("López-Monís") survive because their
    # parts aren't number words.
    tokens = [
        p.lower()
        for t in _tokens_of(text)
        for p in t.split("-") if p
    ]
    if not tokens:
        return True
    meaningful = [t for t in tokens if t not in _LEADING_STOPWORDS]
    if not meaningful:
        return True
    allowed = NUMBER_WORDS | MONTHS | {"numero", "número", "años", "año",
                                       "anos", "dias", "días", "meses",
                                       "plaza", "plazas", "doc", "documento"}
    return all(t in allowed or t.isdigit() for t in meaningful)


_LOCATION_LABELS = frozenset({
    "ES_NER_LOC", "ES_NER_LUGAR", "LOC", "LUGAR",
})


# How much text before the span counts as "context". Long enough to hold a
# title plus a couple of words ("El compareciente ", "Se persona en autos "),
# short enough that an unrelated role word earlier in the sentence does not
# reach across and rescue something it has nothing to do with.
_CONTEXT_CHARS = 48


def _fold_accents(text: str) -> str:
    """Lowercase and strip diacritics, so lexicons need one spelling."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _looks_like_legal_entity(tokens: Sequence[str]) -> bool:
    """True when the span carries a company-form marker.

    Policy: legal-entity names are left in the clear, so a context trigger must
    never rescue one. A sole trader's name carries no marker and is unaffected.
    """
    # Dots are removed anywhere, not just at the edges: "S.A." tokenises to
    # "S.A", whose inner dot would otherwise hide the marker.
    return any(_fold_accents(t).replace(".", "") in ENTITY_MARKERS for t in tokens)


# How many words may sit between the trigger and the span. A trigger governs
# what comes right after it ("El compareciente X", "Firma en prueba de
# conformidad X" — two intervening words), not something further down the line.
# Without this bound, a heading like "COMPARECENCIA DE <nombre> - ACTA NUMERO
# 77" rescued "ACTA NUMERO 77", because the trigger was still inside the
# character window.
_MAX_WORDS_AFTER_TRIGGER = 2


def _words_after_last_trigger(window: str) -> int | None:
    """Words between the last person trigger in ``window`` and its end.

    ``None`` when the window holds no trigger at all. ``window`` must already
    be accent-folded and lowercase.
    """
    end_of_trigger: int | None = None
    for phrase in PERSON_TRIGGER_PHRASES:
        found = window.rfind(phrase)
        if found != -1:
            candidate = found + len(phrase)
            if end_of_trigger is None or candidate > end_of_trigger:
                end_of_trigger = candidate
    for match in re.finditer(r"[a-z]+", window):
        if match.group() in PERSON_TRIGGER_WORDS:
            if end_of_trigger is None or match.end() > end_of_trigger:
                end_of_trigger = match.end()
    if end_of_trigger is None:
        return None
    return len(re.findall(r"[a-z]+", window[end_of_trigger:]))


def strip_leading_person_trigger(text: str) -> tuple[str, int]:
    """Peel a person trigger off the FRONT of a span.

    Returns ``(remaining_text, chars_dropped)``. spaCy sometimes swallows the
    announcing words into the entity itself — the Catalan model tags
    "COMPARECENCIA DE <nombre>" as one ORG span — which hides the trigger from
    the context check and leaves the span boundaries wrong. Mirror image of
    :func:`trim_trailing_role_words`.

    Only leading words that are themselves triggers (plus the connectors
    between them) are removed, and never all of them: at least two tokens must
    survive, otherwise there is no name left to keep.
    """
    tokens = text.split()
    if len(tokens) < 3:
        return text, 0
    connectors = {"de", "del", "la", "el", "los", "las", "a", "en", "por"}

    # Leading phrase first: spaCy's commonest swallow is "COMPARECENCIA DE
    # <nombre>", whose first word is not a trigger word on its own.
    folded_all = _fold_accents(text)
    for phrase in PERSON_TRIGGER_PHRASES_LEADING:
        if folded_all.startswith(phrase):
            remaining = text[len(phrase):].lstrip(" ,;:-")
            if len(remaining.split()) >= 2:
                return remaining, len(text) - len(remaining)

    cut = 0
    for index, token in enumerate(tokens):
        folded = _fold_accents(token).strip(".,;:")
        if folded in PERSON_TRIGGER_WORDS or folded in connectors:
            # A connector alone never starts a trigger run.
            if cut == 0 and folded in connectors:
                break
            cut = index + 1
            continue
        break
    if cut == 0 or len(tokens) - cut < 2:
        return text, 0
    # Trailing connectors belong to the trigger, not the name.
    while cut < len(tokens) and _fold_accents(tokens[cut]).strip(".,;:") in connectors:
        cut += 1
    if len(tokens) - cut < 2:
        return text, 0
    remaining = " ".join(tokens[cut:])
    dropped = len(text) - len(remaining)
    return remaining, max(dropped, 0)


def _resolve_label(spacy_label: str, text: str, context: str) -> str | None:
    """Decide which label a spaCy entity is kept under, or ``None`` to drop it.

    Enabled labels pass through. A disabled ORG gets one second chance: spaCy
    routinely tags an all-caps person name as an organisation, and with ORG
    switched off that name would silently stop being redacted. When the
    preceding text announces a person and the span carries no company-form
    marker, it is kept as PER instead of dropped.

    Found by measurement: disabling ORG cost one name on the synthetic corpus
    ("COMPARECENCIA DE <nombre>", tagged ORG by spaCy), taking the leak count
    from 4 to 5. A readability change must not cost protection.
    """
    if spacy_label in _ENABLED_LABELS:
        return spacy_label
    if spacy_label != "ORG" or "PER" not in _ENABLED_LABELS:
        return None
    tokens = _tokens_of(text.strip().rstrip(".,;"))
    if len(tokens) < 2 or _looks_like_legal_entity(tokens):
        return None
    return "PER" if has_person_trigger(context) else None


def has_person_trigger(context: str) -> bool:
    """True when the text right before a span announces a natural person.

    ``context`` is the document text preceding the span; only the last
    ``_CONTEXT_CHARS`` characters are examined, and the trigger must be within
    ``_MAX_WORDS_AFTER_TRIGGER`` words of the span. Used to rescue name spans
    that the span-only lexicon rules reject for the wrong reason — a surname
    colliding with contract boilerplate ("Banco", "Construcción", "Contrato").
    """
    if not context:
        return False
    window = _fold_accents(context[-_CONTEXT_CHARS:])
    distance = _words_after_last_trigger(window)
    return distance is not None and distance <= _MAX_WORDS_AFTER_TRIGGER


def _in_vocab(token: str, vocab: frozenset[str]) -> bool:
    """Membership test that also tries the singular of a plural token.

    Lets the lexicon carry one form per word: "acreditados" matches
    "acreditado", "obligaciones" matches "obligacion". Only the two common
    Spanish plural suffixes are stripped, and only when the result is long
    enough to still be a word.
    """
    if token in vocab:
        return True
    if token.endswith("es") and len(token) > 4 and token[:-2] in vocab:
        return True
    if token.endswith("s") and len(token) > 3 and token[:-1] in vocab:
        return True
    return False


def _is_generic_token(token: str) -> bool:
    """True if ``token`` is vocabulary the lexicon already knows.

    Used by the "no unknown word" rule: a span made entirely of generic
    vocabulary describes something rather than naming someone.
    """
    if token.isdigit():
        return True
    if token in _FUNCTION_WORDS or token in NUMBER_WORDS or token in MONTHS:
        return True
    return (
        _in_vocab(token, NEVER_IN_NAME)
        or _in_vocab(token, GENERIC_SINGLE)
        or _in_vocab(token, VERBS)
    )


def _is_bare_place(text: str, tokens: list[str]) -> bool:
    """True for a municipality/region name with no address detail.

    Policy (set by the user): a lone toponym ("Barcelona", "Barberá del
    Vallés", "Cantabria") is not personal data — only a full address is. So a
    LOC-shaped span is rejected unless it carries a street-type marker or a
    number, either of which means we're looking at an actual address.
    """
    if len(tokens) > 4:
        return False
    lowered = {t.lower() for t in tokens}
    if lowered & STREET_MARKERS:
        return False
    if any(any(c.isdigit() for c in t) for t in tokens):
        return False
    # Every token must look like a plain capitalised toponym.
    return all(t[:1].isupper() or t.lower() in _LEADING_STOPWORDS
               for t in tokens if t)


def is_probably_false_positive(
    text: str,
    *,
    strict: bool = True,
    label: str | None = None,
    context: str = "",
) -> bool:
    """True if a statistical span should not be redacted.

    ``strict`` enables the shape heuristics tuned for spaCy NER's failure
    modes (all-caps headings, prose runs, leading prepositions). The opf
    transformer passes ``strict=False`` because it is case-agnostic; the
    content-based rejections below still apply to it.

    ``label`` is the source label when known ("ES_NER_LOC", "private_person",
    …). It only gates the bare-toponym rule, which must not fire on person
    names that happen to be short and capitalised.

    ``context`` is the document text immediately preceding the span. When it
    announces a natural person ("El compareciente ", "Se persona en autos "),
    the lexicon rules below are skipped: they reject a whole span for a single
    boilerplate token, and "Banco", "Construcción" and "Contrato" are real
    Spanish surnames. The structural rules above the rescue point still apply,
    so OCR garbage, amounts and statute citations are rejected either way, and
    a span carrying a company-form marker is never rescued (legal-entity names
    are deliberately left in the clear).

    Deterministic recognizers (DNI/IBAN/CIF/registry refs) bypass this check
    entirely — their matches are already structurally validated.
    """
    stripped = text.strip().rstrip(".,;")
    if len(stripped) < 3:
        return True

    # Form labels: trailing ':' or '=' means a field name was swept up.
    if text.rstrip().endswith(_LABEL_TRAILERS):
        return True

    if _FILENAME_RE.fullmatch(stripped):
        return True

    if any(ch in stripped for ch in _STRUCTURAL_CHARS):
        return True

    normalized = _normalized_phrase(text)
    if normalized in PUBLIC_PHRASES or normalized in REGIONS:
        return True

    if _is_amount_or_measure(stripped):
        return True

    if _is_law_citation(stripped):
        return True

    if _is_numeral_phrase(stripped):
        return True

    if _looks_like_ocr_garbage(stripped):
        return True

    tokens = _tokens_of(stripped)
    if not tokens:
        return True
    lowered = [t.lower() for t in tokens]

    # Nothing but known generic vocabulary → a description, not an entity.
    # A real name or address always contributes at least one word the lexicon
    # doesn't know ("Pereda", "Comadrán", "García"). This is the rule that
    # catches "Planta Sótano" / "Parcela Urbana" without a bespoke entry.
    #
    # It sits ABOVE the context rescue on purpose: a span in which every token
    # is known vocabulary is never a name, whatever precedes it. Measured on
    # the synthetic corpus, moving it below let "ACTA NUMERO 77" through when a
    # heading ("COMPARECENCIA DE …") fell inside the context window.
    if all(_is_generic_token(t) for t in lowered):
        return True

    # --- context rescue -------------------------------------------------
    # Everything above is structural or vocabulary-complete: OCR garbage, form
    # labels, filenames, amounts, statute citations, whole-phrase public bodies,
    # all-known-words spans. Those hold no matter what surrounds the span.
    # Everything below rejects on a SINGLE token, which is where real surnames
    # die. If the preceding text announces a person, stop here and keep it.
    if (
        len(tokens) > 1
        and not _looks_like_legal_entity(tokens)
        and has_person_trigger(context)
    ):
        return False

    # Contract/legal boilerplate: one such word anywhere means clause text.
    # A real name contains none of them, so this is safe at any span length.
    # Plurals are matched by also probing the singular, so the lexicon only
    # needs one form per word ("acreditados" → "acreditado").
    if any(_in_vocab(t, NEVER_IN_NAME) for t in lowered):
        return True

    if any(_in_vocab(t, VERBS) for t in lowered):
        return True

    # Bare municipality / region (policy: only full addresses are PII, a lone
    # toponym is not). Applies to single-token LOC too — one word can never be
    # a complete address.
    if label in _LOCATION_LABELS and _is_bare_place(stripped, tokens):
        return True

    # --- Single-token rules ---
    if len(tokens) == 1:
        if _HEADING_ALLCAPS.fullmatch(stripped):
            return True
        if _in_vocab(lowered[0], GENERIC_SINGLE):
            return True
        return False

    # --- Multi-token shape heuristics (spaCy only) ---
    if not strict:
        return False

    # No capitalised token → body prose, not an entity.
    if not any(t[:1].isupper() for t in tokens if t):
        return True

    # All-caps span where function words make up half or more of the tokens →
    # clause heading ("ASI COMO", "LUGAR EN EL QUE", "DE EMPLAZAMIENTO
    # PERSONA A LA QUE SE"). Real all-caps names ("NOELIA LARA MARTIN",
    # "JUAN DE DIOS VALENZUELA") stay well under the threshold.
    alpha_tokens = [t for t in tokens if any(c.isalpha() for c in t)]
    if len(alpha_tokens) >= 2 and all(t.isupper() for t in alpha_tokens):
        func_hits = sum(1 for t in lowered if t in _FUNCTION_WORDS)
        if func_hits * 2 >= len(tokens):
            return True

    # ≥3 tokens starting with a preposition/article → heading fragment.
    if len(tokens) >= 3 and lowered[0] in _LEADING_STOPWORDS:
        return True

    # Two tokens: article/preposition + filler noun ("la Ley", "el Tribunal").
    if len(tokens) == 2 and lowered[0] in _LEADING_STOPWORDS:
        if lowered[1] in _SINGLE_TOKEN_STOPLIST:
            return True

    return False


# Legacy alias — some tests reference the private name.
_is_probably_false_positive = is_probably_false_positive




_CATALAN_CONTRACTION_RE = re.compile(r"^[dlDL]['’]\w+$")


def _is_trimmable_trailing_token(token: str) -> bool:
    """True if ``token`` is a role/label/contraction we can safely peel off."""
    norm = token.lower().rstrip(".,;:")
    if norm in _SINGLE_TOKEN_STOPLIST:
        return True
    # Catalan article contractions ("d'enviament", "l'hospital") are almost
    # never proper nouns; they mark labelled fields in forms.
    if _CATALAN_CONTRACTION_RE.match(token):
        return True
    return False


def trim_trailing_role_words(text: str) -> tuple[str, int]:
    """Peel trailing role/label tokens from a span; return (new_text, chars_dropped).

    Handles cases like "Mateo Ruiz Cano Domicilio" → ("Mateo Ruiz Cano", 10)
    and "Dani Tipus d'enviament" → ("Dani", 20). The caller uses
    ``chars_dropped`` to shrink the span's ``end`` offset so ``text[start:end]``
    still reconstructs the visible entity exactly.
    """
    if not text:
        return text, 0
    trimmed = text.rstrip()
    trailing_ws = len(text) - len(trimmed)
    while True:
        # Find the last whitespace-separated token.
        space_idx = trimmed.rfind(" ")
        newline_idx = trimmed.rfind("\n")
        tab_idx = trimmed.rfind("\t")
        cut = max(space_idx, newline_idx, tab_idx)
        if cut < 0:
            break
        last_token = trimmed[cut + 1:]
        if _is_trimmable_trailing_token(last_token):
            trimmed = trimmed[:cut].rstrip()
            continue
        break
    dropped = len(text) - len(trimmed) - trailing_ws
    # Never drop everything; if we would, keep the original.
    if not trimmed:
        return text, 0
    return trimmed, dropped


def _detect_language(text: str) -> str | None:
    """Best-effort ISO code detection; ``None`` if inconclusive or unavailable."""
    try:
        from langdetect import DetectorFactory, detect  # type: ignore
    except ImportError:
        return None
    # langdetect is randomized by default; pin the seed so the same document
    # never routes to different models on retry.
    DetectorFactory.seed = 0
    sample = text if len(text) < 4000 else text[:4000]
    if len(sample.strip()) < 20:
        return None
    try:
        return detect(sample)
    except Exception:  # noqa: BLE001 — langdetect raises broad exceptions
        return None


def _iter_blocks(text: str):
    """Yield ``(start_offset, block_text)`` per paragraph in the original text.

    Splits on blank-line boundaries. Empty (whitespace-only) blocks are skipped
    but their offset is still consumed so subsequent block offsets stay correct.
    """
    cursor = 0
    for match in _PARAGRAPH_SEP.finditer(text):
        block = text[cursor:match.start()]
        if block.strip():
            yield cursor, block
        cursor = match.end()
    tail = text[cursor:]
    if tail.strip():
        yield cursor, tail


def _select_nlps_for(block: str) -> list:
    """Choose which NER models to run on ``block`` based on its language."""
    if len(_nlps) <= 1 or not _nlps_by_lang:
        return _nlps
    if len(block.strip()) < _MIN_BLOCK_LEN_FOR_DETECT:
        # Too little text to detect reliably — over-detect rather than miss.
        return _nlps
    lang = _detect_language(block)
    if lang and lang in _nlps_by_lang:
        return _nlps_by_lang[lang]
    return _nlps


# A run of this many consecutive all-caps words is treated as a heading or a
# name written in caps, and earns a second NER pass over a case-normalised copy.
_MIN_ALLCAPS_RUN = 2

_ALLCAPS_RUN_RE = re.compile(
    r"(?:\b[A-ZÁÉÍÓÚÜÑÇ][A-ZÁÉÍÓÚÜÑÇ'’-]{2,}\b(?:\s+|$)){%d,}" % _MIN_ALLCAPS_RUN
)


def normalize_allcaps_runs(block: str) -> str | None:
    """Title-case the all-caps runs in ``block``, leaving the rest untouched.

    Returns ``None`` when there is nothing to normalise, or when the result
    would not be the same length as the input — offsets are mapped 1:1 back
    onto the original text, so a length change would corrupt them. ``str.title``
    is length-preserving for Spanish and Catalan, but the check is cheap and a
    silent offset shift would redact the wrong characters.

    Why this exists: the spaCy models are case-sensitive and trained on
    mixed-case text. Measured with ``es_core_news_lg``, the same sentence in
    caps yields fragments ("COMPARECENCIA"/MISC, "LOSTAU"/LOC) while the
    title-cased copy yields the whole person name as PER. Spanish legal and tax
    documents put names in caps constantly — headings, appearances, tables — so
    without this pass those names are never even proposed, and no downstream
    filter can rescue what was never detected.
    """
    if not _ALLCAPS_RUN_RE.search(block):
        return None
    normalised = _ALLCAPS_RUN_RE.sub(lambda m: m.group().title(), block)
    if len(normalised) != len(block) or normalised == block:
        return None
    return normalised


def analyze(text: str, *, strict: bool = True) -> list[NERSpan]:
    """Run per-block language-appropriate NER over the whole document.

    Splits ``text`` into paragraphs, routes each to the matching spaCy model,
    then translates entity offsets back to absolute positions in the original
    string. Duplicate spans (same offset+label) are dropped.

    ``strict`` controls the false-positive filter's aggressiveness — see
    :func:`is_probably_false_positive`. Non-strict mode is used by the
    "aggressive" operating point to keep more spans at the cost of precision.
    """
    if not text:
        return []
    _load()
    if not _nlps:
        return []
    seen: set[tuple[int, int, str]] = set()
    results: list[NERSpan] = []
    for start_off, block in _iter_blocks(text):
        # Two views of the same block: as written, and with all-caps runs
        # title-cased so the case-sensitive models can see names in caps. The
        # normalised view is length-preserving, so entity offsets from it index
        # straight back into the original block; span text is always taken from
        # the original so redaction replaces what the document really contains.
        views = [block]
        normalised = normalize_allcaps_runs(block)
        if normalised is not None:
            views.append(normalised)
        for view in views:
            for nlp in _select_nlps_for(view):
                doc = nlp(view)
                for ent in doc.ents:
                    start_c = ent.start_char
                    end_c = ent.end_char
                    ent_text = block[start_c:end_c]
                    # Public-phrase and content-based filters run on the ORIGINAL
                    # text first, because trimming a form label like "Codi Segur de
                    # Verificació" would leave "Codi Segur de" — no longer a match
                    # for the phrase stoplist.
                    contexto = block[:start_c]
                    # spaCy may have swallowed the announcing words into the span
                    # ("COMPARECENCIA DE <nombre>" as one ORG). Peel them off so
                    # the trigger is visible to the context check and the span
                    # boundaries land on the name itself.
                    if ent.label_ == "ORG" and ent.label_ not in _ENABLED_LABELS:
                        peeled, dropped_front = strip_leading_person_trigger(ent_text)
                        if dropped_front:
                            ent_text = peeled
                            start_c += dropped_front
                            contexto = block[:start_c]
                    label = _resolve_label(ent.label_, ent_text, contexto)
                    if label is None:
                        continue
                    if is_probably_false_positive(
                        ent_text,
                        strict=strict,
                        label=f"ES_NER_{label}",
                        context=contexto,
                    ):
                        continue
                    # Trim trailing role/label tokens ("Mateo Ruiz Cano Domicilio"
                    # → "Mateo Ruiz Cano", "Dani Tipus d'enviament" → "Dani").
                    trimmed_text, dropped = trim_trailing_role_words(ent_text)
                    if dropped:
                        ent_text = trimmed_text
                        end_c -= dropped
                        if end_c <= start_c:
                            continue
                        # Re-check on the trimmed form: after peeling role words
                        # a genuine name may still be left, but a heading remnant
                        # like "Codi Segur de" should be dropped too.
                        if is_probably_false_positive(
                            ent_text,
                            strict=strict,
                            label=f"ES_NER_{label}",
                            context=contexto,
                        ):
                            continue
                    abs_start = start_off + start_c
                    abs_end = start_off + end_c
                    key = (abs_start, abs_end, label)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(NERSpan(
                        start=abs_start,
                        end=abs_end,
                        entity_type=f"ES_NER_{label}",
                        score=_NER_BASELINE_SCORE,
                        text=ent_text,
                    ))
    return results
