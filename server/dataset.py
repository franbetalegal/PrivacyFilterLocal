"""Local gold-set store for measuring detection quality (Phase 7).

Every time a reviewer curates the detected spans in the UI (Phase 6, the
human-in-the-loop flow) and chooses to keep the result as an evaluation
example, we append one record here. Over time this becomes a Spanish-legal
*gold set* we can measure the pipeline against — precision/recall/F1 per entity
type — and later fine-tune on (Phase 8).

Records are stored as JSON Lines in the exact shape ``opf eval`` accepts, so
the same file feeds both our own evaluator (:mod:`server.evaluation`, which
scores the *whole* pipeline) and ``opf eval`` / ``opf train`` (which score the
model alone)::

    {"text": "...", "label": [{"category": "NOMBRE", "start": 10, "end": 21}]}

The file lives under ``PF_DATA_DIR`` (``data/`` by default) and is
**git-ignored**: it contains real PII the firm reviewed, so it must never leave
the machine. Deduplication is by content hash of the text, so re-saving the
same document does not grow the set.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from server import PROJECT_DIR


def _default_path() -> Path:
    override = os.environ.get("PF_GOLD_PATH")
    if override:
        return Path(override)
    data_dir = Path(os.environ.get("PF_DATA_DIR") or (PROJECT_DIR / "data"))
    return data_dir / "gold.jsonl"


@dataclass(frozen=True)
class GoldSpan:
    start: int
    end: int
    label: str

    def to_dict(self) -> dict:
        return {"category": self.label, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class Example:
    text: str
    spans: tuple[GoldSpan, ...]

    def to_record(self) -> dict:
        return {"text": self.text, "label": [s.to_dict() for s in self.spans]}


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _coerce_span(raw) -> GoldSpan | None:
    """Accept a dict from the API (start/end/label or category) → GoldSpan."""
    try:
        start = int(raw["start"])
        end = int(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if end <= start:
        return None
    label = str(raw.get("label") or raw.get("category") or "OTRO").strip() or "OTRO"
    return GoldSpan(start=start, end=end, label=label)


class GoldStore:
    """Thread-safe, append-only JSONL store of evaluation examples."""

    def __init__(self, path: Path | None = None):
        self._path = path or _default_path()
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def _seen_hashes(self) -> set[str]:
        seen: set[str] = set()
        for ex in self.iter_examples():
            seen.add(_text_hash(ex.text))
        return seen

    def append_example(self, text: str, spans: Iterable, *, dedup: bool = True) -> dict:
        """Append one example; return ``{"added": bool, "total": int}``.

        ``spans`` items may be dicts (``start``/``end``/``label``|``category``)
        or any object exposing those attributes. Spans are clamped to the text
        length and stored sorted by start.
        """
        text = text or ""
        if not text.strip():
            return {"added": False, "total": self.count(), "reason": "empty_text"}

        parsed: list[GoldSpan] = []
        for raw in spans:
            if not isinstance(raw, dict):
                raw = {
                    "start": getattr(raw, "start", None),
                    "end": getattr(raw, "end", None),
                    "label": getattr(raw, "label", None),
                }
            span = _coerce_span(raw)
            if span is None:
                continue
            if 0 <= span.start < span.end <= len(text):
                parsed.append(span)
        parsed.sort(key=lambda s: (s.start, s.end))
        example = Example(text=text, spans=tuple(parsed))

        with self._lock:
            if dedup and _text_hash(text) in self._seen_hashes():
                return {"added": False, "total": self.count(), "reason": "duplicate"}
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(example.to_record(), ensure_ascii=False)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            return {"added": True, "total": self.count()}

    def iter_examples(self) -> Iterator[Example]:
        try:
            fh = open(self._path, encoding="utf-8")
        except (OSError, FileNotFoundError):
            return
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield _record_to_example(record)

    def load_examples(self) -> list[Example]:
        return list(self.iter_examples())

    def count(self) -> int:
        return sum(1 for _ in self.iter_examples())

    def stats(self) -> dict:
        """Summary for the UI: number of examples and spans per label."""
        n = 0
        by_label: dict[str, int] = {}
        for ex in self.iter_examples():
            n += 1
            for s in ex.spans:
                by_label[s.label] = by_label.get(s.label, 0) + 1
        return {
            "examples": n,
            "spans": sum(by_label.values()),
            "by_label": dict(sorted(by_label.items())),
            "path": str(self._path),
        }


def _record_to_example(record: dict) -> Example:
    text = str(record.get("text", ""))
    spans: list[GoldSpan] = []
    # Preferred: the "label" list form we write.
    for entry in record.get("label") or []:
        if not isinstance(entry, dict):
            continue
        span = _coerce_span(entry)
        if span is not None:
            spans.append(span)
    # Also accept the alternate opf "spans" mapping form {label: [[s, e], ...]}.
    raw_spans = record.get("spans")
    if isinstance(raw_spans, dict):
        for key, offsets in raw_spans.items():
            label = str(key).split(": ", 1)[0]
            if not isinstance(offsets, Sequence):
                continue
            for off in offsets:
                if isinstance(off, Sequence) and len(off) == 2:
                    span = _coerce_span({"start": off[0], "end": off[1], "label": label})
                    if span is not None:
                        spans.append(span)
    spans.sort(key=lambda s: (s.start, s.end))
    return Example(text=text, spans=tuple(spans))


# --- module-level default store -------------------------------------------
_store: GoldStore | None = None
_store_lock = threading.Lock()


def get_store() -> GoldStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = GoldStore()
    return _store
