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
      ``PER,LOC,ORG``). Add ``MISC`` if the domain benefits from it.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass

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

_ENABLED_LABELS = frozenset(
    label.strip() for label in os.environ.get("PF_NER_LABELS", "PER,LOC,ORG").split(",")
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


# Words that spaCy's Iberian NER (or opf) routinely mis-classifies as entities
# in formal writs because they are OOV or capitalized as headings. Only applies
# to *single-token* candidates — a multi-token span like "Plaza Mayor de
# Madrid" is almost certainly a real place; a lone "Plaza" is a heading.
_SINGLE_TOKEN_STOPLIST = frozenset({
    # Structural / heading words — ES
    "trámite", "trámit", "tramit", "nombre", "nom",
    "instrucción", "instrucció", "instruccio",
    "admite", "admet", "denegado", "denegada", "denegat",
    "nulidad", "nul·litat", "nulitat",
    "plaza", "plaça", "calle", "carrer", "avenida", "avinguda",
    "número", "numero", "núm", "núm.",
    "página", "pàgina", "folio", "foli",
    "artículo", "article", "art", "art.",
    "expediente", "expedient", "procedimiento", "procediment",
    "juzgado", "jutjat", "tribunal", "audiencia", "audiència",
    "sentencia", "sentència", "auto", "acuerdo", "acord",
    "hecho", "fet", "hechos", "fets",
    "fundamento", "fundamentos", "fonament", "fonaments",
    "derecho", "dret", "vistos", "resuelvo", "resolc",
    "señor", "señora", "senyor", "senyora", "sr", "sra",
    "don", "doña", "sr.", "sra.",
    "parte", "partes", "part", "parts",
    "ministerio", "ministeri", "consejería", "conselleria",
    "razón", "raó",
    # Roles / labels commonly appended to a name in a form
    "letrado", "letrada", "advocat", "advocada",
    "procurador", "procuradora",
    "abogado", "abogada",
    "demandante", "demandada", "demandant", "demandada",
    "demandado", "demandat", "denunciante",
    "testigo", "perito", "perita",
    "domicilio", "domicili", "adreça", "direcció", "dirección",
    "beneficiario", "beneficiaria", "beneficiària",
    "órgano", "organ", "orgue",
    # Catalan form-label vocabulary (this file's biggest new source of noise)
    "dades", "annexats", "annexat", "identificació", "identificacio",
    "sol·licitud", "sol-licitud", "sollicitud", "solicitud",
    "algorisme", "hash", "signatura", "signat", "signada",
    "data", "hora", "codi", "segur", "verificació", "verificacion",
    "document", "documents", "doc",
    "poder", "judicial", "civil", "penal", "fiscal",
    "unitat", "deganat", "secció", "servei", "servicio",
    "comú", "comu", "comuna",
    "general", "jurisdicció", "classe", "class",
    "consignaciones", "consignacions", "oficina",
    "parlamento", "parlament", "consejo", "consell",
    "colegios", "col·legis", "collegis",
    "generalitat", "departament", "departamento",
    "ministerio", "ministeri", "conselleria",
    "administración", "administració",
    "advierto", "dése", "dese", "adjunte", "adjuntar",
    # Catalan common words / contractions that shouldn't be entities alone
    "tipus", "enviament", "adreça", "adreca",
    # Legal-code references
    "lec", "lc", "lot", "loj", "lopj", "cc", "cp", "ce",
    # Verbs / short filler that should never be an entity on its own
    "rut", "nom", "adreça", "número",
})

_HEADING_ALLCAPS = re.compile(r"^[A-ZÁÉÍÓÚÜÑÇ][A-ZÁÉÍÓÚÜÑÇ0-9]{2,}$")

# Prepositions / articles that never start a real proper-noun run in ES/CA.
# Applied only when the span has ≥3 tokens so genuine two-token entities like
# "El Escorial" or "La Rioja" survive.
_LEADING_STOPWORDS = frozenset({
    # Spanish
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "al", "a", "en", "por", "para", "con", "sin", "entre", "sobre",
    "hacia", "desde", "hasta", "que", "como",
    # Catalan
    "dels", "les", "els", "amb", "per", "sense", "sota", "davant",
    "darrere", "cap", "fins", "des",
})

# Function words that dominate a heading fragment but are rare inside person
# names. Real names may contain "de la" (Juan de la Cruz) but never "EN", "SE",
# "QUE" — so the ratio of function words distinguishes headings from names.
_FUNCTION_WORDS = frozenset({
    # Spanish
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "al", "a", "en", "por", "para", "con", "sin", "entre", "sobre",
    "hacia", "desde", "hasta", "que", "como", "se", "no", "es", "y", "o",
    "lo", "su", "sus", "le", "les",
    # Catalan
    "dels", "les", "els", "amb", "per", "sense", "sota", "davant",
    "cap", "fins", "des", "com", "i", "o", "es", "seu", "seus", "no",
    "ho", "que",
})

# Trailing punctuation that turns a span into a form-label instead of an entity
# ("Codi Segur de Verificació:", "Nombre completo -").
_LABEL_TRAILERS = (":", "=")

# Multi-token phrases that reference PUBLIC institutions, geographic regions or
# legal-code identifiers. These are NOT personal data under GDPR — redacting
# them would strip the document of its context without protecting any natural
# person. Matched case-insensitively and after collapsing whitespace.
_PUBLIC_ENTITY_PHRASES = frozenset(p.lower() for p in {
    # Spanish public institutions and branches
    "administración de justicia",
    "ministerio fiscal",
    "poder judicial",
    "parlamento europeo",
    "consejo europeo",
    "consejo",
    "oficina judicial",
    "asistencia jurídica gratuita",
    "consejo general del poder judicial",
    "cortes generales",
    "audiencia nacional",
    "audiencia provincial",
    "tribunal supremo",
    "tribunal constitucional",
    "boletín oficial del estado",
    "boe",
    # Catalan public institutions
    "administració de justícia",
    "generalitat de catalunya",
    "generalitat",
    "parlament de catalunya",
    "tribunal superior de justícia de catalunya",
    "col·legi d'advocats",
    "col·legi de procuradors",
    "diari oficial de la generalitat de catalunya",
    "dogc",
    # Common Catalan form-label phrases (not personal data)
    "codi segur de verificació",
    "algorisme hash",
    "signatura electrònica",
    "número de sol·licitud", "número de sol-licitud",
    "servei comú", "servei comú general", "servei comú data",
    "secció civil", "secció penal",
    "jutjat de primera instància",
    "jutjat de primera instància i instrucció",
    "unitat processal de suport directe",
    "administració pública",
    # Public regions
    "cataluña", "catalunya",
    "españa", "espanya",
    "unión europea", "unió europea",
    # Legal-code references frequently tagged as ORG/LOC
    "código civil", "código penal", "código de comercio",
    "constitución española",
    "ley de enjuiciamiento civil", "ley de enjuiciamiento criminal",
})

# File-name extensions the NER sometimes wraps into ORG/PER (annex names).
_FILENAME_RE = re.compile(
    r".+\.(pdf|docx?|xlsx?|pptx?|txt|csv|jpe?g|png|tiff?|rtf|odt|ods|odp)$",
    re.IGNORECASE,
)

# OCR garbage: a long-ish alphanumeric run with no lowercase letters (mixed
# caps and digits) is almost never a real Spanish/Catalan entity. Applied only
# when the span is either single-token OR the whole span contains no lowercase.
_OCR_GARBAGE_MIN_LEN = 15


def _normalized_phrase(text: str) -> str:
    """Lowercase and collapse whitespace/punctuation for phrase-stoplist lookup."""
    cleaned = re.sub(r"\s+", " ", text.strip().strip(".,;:()[]"))
    return cleaned.lower()


def _looks_like_ocr_garbage(text: str) -> bool:
    """True for long strings with no lowercase letters and any digits/mix.

    Catches OCR-mangled hashes and identifiers like
    ``F616YBCN9F SABLBGRVVVVM7Z06X956V7A`` that spaCy sometimes flags as ORG.
    """
    stripped = text.strip()
    if len(stripped) < _OCR_GARBAGE_MIN_LEN:
        return False
    if any(c.islower() for c in stripped):
        return False
    return any(c.isdigit() for c in stripped) and any(c.isalpha() for c in stripped)


def is_probably_false_positive(text: str, *, strict: bool = True) -> bool:
    """True if a statistical (NER or opf) span looks like a heading, filler,
    filename, public institution, or OCR garbage — anything that redacting
    would remove document context without protecting personal data.

    ``strict=True`` (default) applies the full ruleset, including case and
    positional heuristics designed for spaCy NER (whose false-positive shapes
    are more predictable). ``strict=False`` uses only the source-independent
    rules — content-based rejections that hold for any statistical model.
    The opf transformer, which is case-agnostic and language-agnostic, is
    filtered in non-strict mode so it can still catch legitimate lowercase
    mentions of a person while public-entity / filename / garbage rejections
    stay in force.

    The deterministic recognizers (DNI/IBAN/CIF/…) bypass this check; their
    matches are already structurally validated.
    """
    stripped = text.strip().rstrip(".,;")
    if len(stripped) < 3:
        return True

    # Form labels: "Codi Segur de Verificació:" — trailing punct is a strong
    # signal the tagger swept up a field name, not an entity.
    if text.rstrip().endswith(_LABEL_TRAILERS):
        return True

    # File names in annex references ("bancaria.pdf", "gastos.pdf").
    if _FILENAME_RE.fullmatch(stripped):
        return True

    # Structural characters that never appear inside a person / place / org
    # name in Spanish/Catalan writs ("LEC).La", "753 LEC.). Doc"). Parentheses
    # and slashes mid-string indicate OCR corruption or fragment concatenation.
    if any(ch in stripped for ch in "()[]{}/\\"):
        return True

    # Public institutions / regions / legal-code references — not PII.
    if _normalized_phrase(text) in _PUBLIC_ENTITY_PHRASES:
        return True

    # OCR-garbage-shaped runs.
    if _looks_like_ocr_garbage(stripped):
        return True

    tokens = stripped.split()

    # --- Single-token rules ---
    if len(tokens) == 1:
        if _HEADING_ALLCAPS.fullmatch(stripped):
            return True
        if stripped.lower() in _SINGLE_TOKEN_STOPLIST:
            return True
        return False

    # --- Multi-token rules ---
    # These are aggressive shape-based heuristics tuned for spaCy NER's
    # failure modes; opf (which passes strict=False) skips them.
    if not strict:
        return False

    # 1. No capitalized token anywhere → body prose, not an entity.
    #    ("garantit amb signatura-e", "compareix davant el jutjat")
    if not any(t and t[0].isupper() for t in tokens):
        return True

    # 2. Three or more all-caps tokens WHERE more than half are function words
    #    → heading fragment ("LUGAR EN EL QUE", "DE EMPLAZAMIENTO PERSONA A LA
    #    QUE SE"). Real all-caps names like "NOELIA LARA MARTIN" or "JUAN DE LA
    #    CRUZ" don't cross the function-word ratio threshold.
    alpha_tokens = [t for t in tokens if any(c.isalpha() for c in t)]
    if len(alpha_tokens) >= 3 and all(t.isupper() for t in alpha_tokens):
        func_hits = sum(1 for t in tokens if t.lower() in _FUNCTION_WORDS)
        if func_hits * 2 > len(tokens):
            return True

    # 3. ≥3 tokens starting with a preposition/article → heading fragment.
    #    ("DEL EMPLAZAMIENTO Comparecer"). Two-token "El Escorial" survives.
    if len(tokens) >= 3 and tokens[0].lower().rstrip(".,") in _LEADING_STOPWORDS:
        return True

    return False


# Legacy alias — some tests still reference the private name.
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


def analyze(text: str) -> list[NERSpan]:
    """Run per-block language-appropriate NER over the whole document.

    Splits ``text`` into paragraphs, routes each to the matching spaCy model,
    then translates entity offsets back to absolute positions in the original
    string. Duplicate spans (same offset+label) are dropped.
    """
    if not text:
        return []
    _load()
    if not _nlps:
        return []
    seen: set[tuple[int, int, str]] = set()
    results: list[NERSpan] = []
    for start_off, block in _iter_blocks(text):
        for nlp in _select_nlps_for(block):
            doc = nlp(block)
            for ent in doc.ents:
                if ent.label_ not in _ENABLED_LABELS:
                    continue
                ent_text = ent.text
                start_c = ent.start_char
                end_c = ent.end_char
                # Public-phrase and content-based filters run on the ORIGINAL
                # text first, because trimming a form label like "Codi Segur de
                # Verificació" would leave "Codi Segur de" — no longer a match
                # for the phrase stoplist.
                if is_probably_false_positive(ent_text):
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
                    if is_probably_false_positive(ent_text):
                        continue
                abs_start = start_off + start_c
                abs_end = start_off + end_c
                key = (abs_start, abs_end, ent.label_)
                if key in seen:
                    continue
                seen.add(key)
                results.append(NERSpan(
                    start=abs_start,
                    end=abs_end,
                    entity_type=f"ES_NER_{ent.label_}",
                    score=_NER_BASELINE_SCORE,
                    text=ent_text,
                ))
    return results
