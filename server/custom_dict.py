"""User-editable custom dictionary of terms to always anonymize.

This is a fourth, *deterministic* layer of the pipeline (alongside the Spanish
recognizers in :mod:`server.recognizers_es`). Any term the user adds here is
redacted unconditionally and, like the validated recognizers, **bypasses the
false-positive filter** — the user has explicitly asserted it is PII, so there
is nothing to second-guess. This is the reliable way to cover case parties and
entities the statistical models miss (e.g. ``STEEL PROPERTY SL``).

The dictionary is stored locally as JSON (never leaves the machine) and grows
with use. It can be exported and imported to share and *complement* dictionaries
between users; import does a **smart merge** (union without duplicates, keeping
the local entry on conflict).

Matching modes (per entry):

* ``smart``  — case- and accent-insensitive, whole-word, tolerant of repeated
  whitespace between the term's words. The default and the recommended mode.
* ``exact``  — case-sensitive, whole-word, literal.
* ``regex``  — the term is a Python regular expression applied to the raw text
  (advanced; invalid patterns are skipped and reported by :func:`validate`).

``analyze(text)`` returns :class:`server.recognizers_es.Recognition` objects so
the spans plug straight into the pipeline's deterministic source.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from server import PROJECT_DIR
from server.recognizers_es import Recognition
from server import messages

# --------------------------------------------------------------------------
#  Storage location
# --------------------------------------------------------------------------
# PF_DICT_PATH overrides the file outright; otherwise it lives under PF_DATA_DIR
# (set by the portable launcher to stay inside the app folder) or <project>/data.
def _default_path() -> Path:
    override = os.environ.get("PF_DICT_PATH")
    if override:
        return Path(override)
    data_dir = Path(os.environ.get("PF_DATA_DIR") or (PROJECT_DIR / "data"))
    return data_dir / "custom_terms.json"


MATCH_MODES = ("smart", "exact", "regex")
DEFAULT_MATCH = "smart"

# Canonical Spanish labels offered in the UI. The list is *extensible*: a user
# may attach any label (uppercased + sanitized) and it flows through the
# pipeline's placeholder machinery unchanged, since ``_friendly`` falls back to
# ``label.upper()`` for unknown labels.
CANONICAL_LABELS = (
    "NOMBRE", "EMPRESA", "ORGANIZACION", "DIRECCION", "LUGAR",
    "TELEFONO", "EMAIL", "DNI", "NIE", "NIF", "IBAN",
    "REFERENCIA", "EXPEDIENTE", "OTRO",
)

_LABEL_RE = re.compile(r"[^0-9A-ZÁÉÍÓÚÑÜ_]+")


def normalize_label(label: str) -> str:
    """Uppercase and sanitize a label to the placeholder-safe shape ``EMPRESA``."""
    s = (label or "").strip().upper().replace(" ", "_")
    s = _LABEL_RE.sub("_", s).strip("_")
    return s or "OTRO"


# --------------------------------------------------------------------------
#  Accent/case folding with an exact offset map
# --------------------------------------------------------------------------
def _fold_with_map(text: str) -> tuple[str, list[int]]:
    """Return a case/accent-folded copy of ``text`` plus an index map.

    ``index_map`` has ``len(folded) + 1`` entries; ``index_map[i]`` is the
    offset in the *original* ``text`` where folded character ``i`` began, and
    the last entry is ``len(text)``. This lets a match found in the folded
    string be mapped back to exact original offsets, even though folding a
    character (e.g. ``é`` → ``e``, ``ﬁ`` → ``fi``) may change the length.
    """
    folded_chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        decomposed = unicodedata.normalize("NFKD", ch)
        for d in decomposed:
            if unicodedata.combining(d):
                continue  # drop accents/diacritics
            folded_chars.append(d.casefold())
            index_map.append(i)
    index_map.append(len(text))
    return "".join(folded_chars), index_map


def _word_boundary_regex(folded_term: str) -> re.Pattern:
    """Build a whole-word regex for a folded term, tolerant of inner whitespace."""
    # Split on whitespace, escape each token, rejoin allowing 1+ whitespace so
    # "STEEL   PROPERTY" in the document still matches "STEEL PROPERTY".
    tokens = folded_term.split()
    if not tokens:
        return re.compile(r"(?!x)x")  # matches nothing
    body = r"\s+".join(re.escape(t) for t in tokens)
    # Boundaries against word characters (letters/digits/underscore), so
    # punctuation next to the term (commas, parentheses) does not block a match,
    # but "STEELWORKS" / "ACERO_STEEL" do not match "STEEL". The folded text is
    # lowercase ASCII, hence [0-9a-z_].
    return re.compile(rf"(?<![0-9a-z_]){body}(?![0-9a-z_])")


# --------------------------------------------------------------------------
#  Entry model
# --------------------------------------------------------------------------
@dataclass
class Entry:
    term: str
    label: str = "OTRO"
    match: str = DEFAULT_MATCH
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "term": self.term,
            "label": self.label,
            "match": self.match,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        match = str(d.get("match", DEFAULT_MATCH)).lower()
        if match not in MATCH_MODES:
            match = DEFAULT_MATCH
        return cls(
            term=str(d.get("term", "")).strip(),
            label=normalize_label(str(d.get("label", "OTRO"))),
            match=match,
            enabled=bool(d.get("enabled", True)),
            id=str(d.get("id") or uuid.uuid4().hex),
        )

    def dedup_key(self) -> tuple[str, str]:
        """Two entries collide when they'd match the same thing the same way."""
        if self.match == "exact":
            key = self.term
        elif self.match == "regex":
            key = self.term
        else:  # smart: fold so "García" and "GARCIA" collapse
            key = _fold_with_map(self.term)[0].strip()
        return (self.match, key)


def validate_entry(term: str, match: str) -> str | None:
    """Return an error message if the entry is unusable, else ``None``."""
    if not term or not term.strip():
        return messages.message(messages.TERM_EMPTY)
    if match == "regex":
        try:
            re.compile(term)
        except re.error as exc:
            return messages.message(messages.INVALID_REGEX, error=str(exc))
    return None


# --------------------------------------------------------------------------
#  Compiled matcher (built once per store snapshot)
# --------------------------------------------------------------------------
@dataclass
class _Matcher:
    entry: Entry
    regex: re.Pattern
    on_folded: bool  # True → run against the folded text + map offsets back


def _build_matcher(entry: Entry) -> _Matcher | None:
    if not entry.enabled:
        return None
    term = entry.term.strip()
    if not term:
        return None
    if entry.match == "regex":
        try:
            return _Matcher(entry, re.compile(term), on_folded=False)
        except re.error:
            return None
    if entry.match == "exact":
        tokens = term.split()
        body = r"\s+".join(re.escape(t) for t in tokens)
        return _Matcher(entry, re.compile(rf"(?<!\w){body}(?!\w)"), on_folded=False)
    # smart
    folded_term, _ = _fold_with_map(term)
    return _Matcher(entry, _word_boundary_regex(folded_term), on_folded=True)


# --------------------------------------------------------------------------
#  Store
# --------------------------------------------------------------------------
class Store:
    """Thread-safe, file-backed collection of dictionary entries."""

    def __init__(self, path: Path | None = None):
        self._path = path or _default_path()
        self._lock = threading.RLock()
        self._entries: list[Entry] = []
        self._matchers: list[_Matcher] = []
        self._loaded = False

    # -- persistence -------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._entries = self._read_file()
            self._rebuild_matchers()
            self._loaded = True

    def _read_file(self) -> list[Entry]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        items = data.get("terms", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        out: list[Entry] = []
        for item in items:
            if isinstance(item, dict) and str(item.get("term", "")).strip():
                out.append(Entry.from_dict(item))
        return out

    def _write_file(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "terms": [e.to_dict() for e in self._entries],
        }
        # Atomic replace so a crash mid-write can't corrupt the dictionary.
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".custom_terms_", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _rebuild_matchers(self) -> None:
        self._matchers = [
            m for m in (_build_matcher(e) for e in self._entries) if m is not None
        ]

    # -- queries -----------------------------------------------------------
    def all_entries(self) -> list[Entry]:
        self._ensure_loaded()
        with self._lock:
            return list(self._entries)

    def export_terms(self) -> list[dict]:
        return [e.to_dict() for e in self.all_entries()]

    # -- mutations ---------------------------------------------------------
    def add(self, term: str, label: str = "OTRO",
            match: str = DEFAULT_MATCH, enabled: bool = True) -> Entry:
        self._ensure_loaded()
        entry = Entry.from_dict(
            {"term": term, "label": label, "match": match, "enabled": enabled}
        )
        with self._lock:
            existing = self._find_by_key(entry.dedup_key())
            if existing is not None:
                # Same match target already present → update label/enabled in
                # place rather than creating a duplicate.
                existing.label = entry.label
                existing.enabled = entry.enabled
                self._persist_locked()
                return existing
            self._entries.append(entry)
            self._persist_locked()
            return entry

    def update(self, entry_id: str, **fields) -> Entry | None:
        self._ensure_loaded()
        with self._lock:
            entry = self._by_id(entry_id)
            if entry is None:
                return None
            if "term" in fields:
                entry.term = str(fields["term"]).strip()
            if "label" in fields:
                entry.label = normalize_label(str(fields["label"]))
            if "match" in fields:
                m = str(fields["match"]).lower()
                entry.match = m if m in MATCH_MODES else entry.match
            if "enabled" in fields:
                entry.enabled = bool(fields["enabled"])
            self._persist_locked()
            return entry

    def remove(self, entry_id: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.id != entry_id]
            if len(self._entries) == before:
                return False
            self._persist_locked()
            return True

    def import_terms(self, items: Iterable[dict]) -> dict:
        """Smart-merge external entries; keep local on conflict.

        Returns counts: ``{"added": n, "skipped": n}`` where ``skipped`` is the
        number of incoming entries already covered by a local entry.
        """
        self._ensure_loaded()
        added = skipped = invalid = 0
        with self._lock:
            keys = {e.dedup_key() for e in self._entries}
            for item in items:
                if not isinstance(item, dict):
                    invalid += 1
                    continue
                candidate = Entry.from_dict(item)
                if not candidate.term:
                    invalid += 1
                    continue
                if validate_entry(candidate.term, candidate.match) is not None:
                    invalid += 1
                    continue
                key = candidate.dedup_key()
                if key in keys:
                    skipped += 1
                    continue
                # Fresh id so imported entries never collide with local ones.
                candidate.id = uuid.uuid4().hex
                self._entries.append(candidate)
                keys.add(key)
                added += 1
            if added:
                self._persist_locked()
        return {"added": added, "skipped": skipped, "invalid": invalid}

    def clear(self) -> None:
        self._ensure_loaded()
        with self._lock:
            self._entries = []
            self._persist_locked()

    # -- detection ---------------------------------------------------------
    def analyze(self, text: str) -> list[Recognition]:
        """Find every enabled term in ``text`` and return validated spans."""
        if not text:
            return []
        self._ensure_loaded()
        with self._lock:
            matchers = list(self._matchers)
        if not matchers:
            return []
        folded, index_map = _fold_with_map(text)
        hits: list[Recognition] = []
        for m in matchers:
            haystack = folded if m.on_folded else text
            for match in m.regex.finditer(haystack):
                if m.on_folded:
                    start = index_map[match.start()]
                    end = index_map[match.end()]
                else:
                    start, end = match.start(), match.end()
                if end <= start:
                    continue
                hits.append(Recognition(
                    start=start,
                    end=end,
                    entity_type=m.entry.label,
                    score=1.0,
                    text=text[start:end],
                ))
        return hits

    # -- internal (must hold the lock) -------------------------------------
    def _persist_locked(self) -> None:
        self._rebuild_matchers()
        self._write_file()

    def _by_id(self, entry_id: str) -> Entry | None:
        for e in self._entries:
            if e.id == entry_id:
                return e
        return None

    def _find_by_key(self, key: tuple[str, str]) -> Entry | None:
        for e in self._entries:
            if e.dedup_key() == key:
                return e
        return None


# --------------------------------------------------------------------------
#  Module-level default store used by the pipeline and API
# --------------------------------------------------------------------------
_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = Store()
    return _store


def analyze(text: str) -> list[Recognition]:
    """Convenience entry point mirroring ``recognizers_es.analyze``."""
    return get_store().analyze(text)
