"""Tests for the gold-set store (server.dataset)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.dataset import GoldStore, Example, GoldSpan


@pytest.fixture
def store(tmp_path: Path) -> GoldStore:
    return GoldStore(path=tmp_path / "gold.jsonl")


class _Span:
    def __init__(self, start, end, label):
        self.start, self.end, self.label = start, end, label


def test_append_and_load(store: GoldStore):
    text = "Con DNI 12345678Z firma."
    res = store.append_example(text, [{"start": 8, "end": 17, "label": "DNI"}])
    assert res == {"added": True, "total": 1}
    examples = store.load_examples()
    assert len(examples) == 1
    assert examples[0].text == text
    assert examples[0].spans == (GoldSpan(8, 17, "DNI"),)


def test_append_accepts_span_objects_and_category_key(store: GoldStore):
    store.append_example("Hola Ana.", [_Span(5, 8, "NOMBRE")])
    store.append_example("Hola Eva.", [{"start": 5, "end": 8, "category": "NOMBRE"}])
    labels = [s.label for ex in store.load_examples() for s in ex.spans]
    assert labels == ["NOMBRE", "NOMBRE"]


def test_dedup_by_text(store: GoldStore):
    text = "Documento repetido."
    assert store.append_example(text, [])["added"] is True
    second = store.append_example(text, [{"start": 0, "end": 9, "label": "X"}])
    assert second["added"] is False
    assert second["reason"] == "duplicate"
    assert store.count() == 1


def test_empty_text_rejected(store: GoldStore):
    res = store.append_example("   ", [{"start": 0, "end": 1, "label": "X"}])
    assert res["added"] is False
    assert res["reason"] == "empty_text"


def test_out_of_range_spans_are_dropped(store: GoldStore):
    store.append_example("abc", [
        {"start": 0, "end": 2, "label": "OK"},
        {"start": 2, "end": 99, "label": "TOOLONG"},   # end past text
        {"start": 5, "end": 4, "label": "REVERSED"},   # end<=start
    ])
    spans = store.load_examples()[0].spans
    assert [s.label for s in spans] == ["OK"]


def test_spans_stored_sorted(store: GoldStore):
    store.append_example("abcdef", [
        {"start": 4, "end": 6, "label": "B"},
        {"start": 0, "end": 2, "label": "A"},
    ])
    spans = store.load_examples()[0].spans
    assert [s.start for s in spans] == [0, 4]


def test_stats(store: GoldStore):
    store.append_example("Con DNI 12345678Z.", [{"start": 8, "end": 17, "label": "DNI"}])
    store.append_example("Ana y Eva.", [
        {"start": 0, "end": 3, "label": "NOMBRE"},
        {"start": 6, "end": 9, "label": "NOMBRE"},
    ])
    stats = store.stats()
    assert stats["examples"] == 2
    assert stats["spans"] == 3
    assert stats["by_label"] == {"DNI": 1, "NOMBRE": 2}


def test_persists_across_instances(tmp_path: Path):
    p = tmp_path / "g.jsonl"
    GoldStore(path=p).append_example("uno.", [])
    GoldStore(path=p).append_example("dos.", [])
    assert GoldStore(path=p).count() == 2


def test_reads_alternate_spans_mapping_form(tmp_path: Path):
    """The opf 'spans' mapping form must also load."""
    p = tmp_path / "g.jsonl"
    p.write_text('{"text": "Hola Ana.", "spans": {"NOMBRE": [[5, 8]]}}\n')
    ex = GoldStore(path=p).load_examples()
    assert ex[0].spans == (GoldSpan(5, 8, "NOMBRE"),)


# --- API endpoints ---------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from server import dataset

    s = GoldStore(path=tmp_path / "gold.jsonl")
    monkeypatch.setattr(dataset, "get_store", lambda: s)
    from server.main import app
    try:
        return TestClient(app), s
    except TypeError as exc:
        pytest.skip(f"TestClient unavailable: {exc}")


def test_api_capture_and_stats(client):
    c, _store = client
    r = c.post("/api/dataset/capture", json={
        "text": "Con DNI 12345678Z.",
        "spans": [{"start": 8, "end": 17, "label": "DNI"}],
    })
    assert r.status_code == 200
    assert r.json()["added"] is True

    r = c.get("/api/dataset/stats")
    assert r.json()["examples"] == 1
    assert r.json()["by_label"] == {"DNI": 1}


def test_api_evaluate_empty_gold(client):
    c, _store = client
    r = c.post("/api/dataset/evaluate", json={"mode": "balanced"})
    assert r.json()["examples"] == 0


def test_api_evaluate_scores(client, monkeypatch):
    c, store = client
    from opf._api import RedactionResult
    from opf._core.runtime import DetectedSpan
    from server import inference

    text = "Con DNI 12345678Z."
    store.append_example(text, [{"start": 8, "end": 17, "label": "DNI"}])

    async def fake_redact(t, mode="balanced"):
        span = DetectedSpan(label="DNI", start=8, end=17,
                            text="12345678Z", placeholder="[DNI_1]")
        return RedactionResult(
            schema_version="1", summary={}, text=t,
            detected_spans=(span,),
            redacted_text=t.replace("12345678Z", "[DNI_1]"), warning=None,
        )
    monkeypatch.setattr(inference, "redact", fake_redact)

    r = c.post("/api/dataset/evaluate", json={"mode": "balanced"})
    body = r.json()
    assert body["examples"] == 1
    assert body["overall"]["recall"] == 1.0
    assert body["leak_count"] == 0
