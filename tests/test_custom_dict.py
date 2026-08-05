"""Tests for the user-editable custom dictionary (server.custom_dict)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import custom_dict
from server.custom_dict import Store, _fold_with_map, normalize_label


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(path=tmp_path / "custom_terms.json")


# --- folding / offset map --------------------------------------------------

def test_fold_lowercases_and_strips_accents():
    folded, _ = _fold_with_map("García SÁNCHEZ")
    assert folded == "garcia sanchez"


def test_fold_map_recovers_original_offsets():
    text = "El Sr. García firmó."
    folded, index_map = _fold_with_map(text)
    i = folded.index("garcia")
    start = index_map[i]
    end = index_map[i + len("garcia")]
    assert text[start:end] == "García"


# --- smart matching --------------------------------------------------------

def test_smart_match_is_case_and_accent_insensitive(store: Store):
    store.add("García", label="NOMBRE")
    hits = store.analyze("Ayer GARCIA y garcía firmaron el contrato.")
    assert len(hits) == 2
    assert all(h.entity_type == "NOMBRE" for h in hits)
    # Offsets map back to the exact original substrings.
    assert {h.text for h in hits} == {"GARCIA", "garcía"}


def test_smart_match_is_whole_word(store: Store):
    store.add("STEEL", label="EMPRESA")
    hits = store.analyze("STEEL pero no STEELWORKS ni ACERO_STEEL")
    assert len(hits) == 1
    assert hits[0].text == "STEEL"


def test_smart_match_tolerates_extra_whitespace(store: Store):
    store.add("STEEL PROPERTY SL", label="EMPRESA")
    hits = store.analyze("La mercantil STEEL   PROPERTY  SL comparece.")
    assert len(hits) == 1
    assert hits[0].text == "STEEL   PROPERTY  SL"


def test_smart_match_survives_surrounding_punctuation(store: Store):
    store.add("HARDENED STEEL SL", label="EMPRESA")
    hits = store.analyze("(HARDENED STEEL SL),")
    assert len(hits) == 1
    assert hits[0].text == "HARDENED STEEL SL"


# --- exact / regex ---------------------------------------------------------

def test_exact_match_is_case_sensitive(store: Store):
    store.add("Steel", label="EMPRESA", match="exact")
    hits = store.analyze("Steel y steel y STEEL")
    assert len(hits) == 1
    assert hits[0].text == "Steel"


def test_regex_match(store: Store):
    store.add(r"EXP-\d{4}/\d{2}", label="EXPEDIENTE", match="regex")
    hits = store.analyze("Autos EXP-2025/07 y EXP-1999/12.")
    assert {h.text for h in hits} == {"EXP-2025/07", "EXP-1999/12"}


def test_invalid_regex_is_skipped_not_crash(store: Store):
    store.add("(unclosed", label="OTRO", match="regex")
    # Invalid pattern → no matcher built → analyze returns nothing, no error.
    assert store.analyze("(unclosed here") == []


def test_disabled_entry_is_not_matched(store: Store):
    e = store.add("García", label="NOMBRE")
    store.update(e.id, enabled=False)
    assert store.analyze("García firmó") == []


# --- persistence -----------------------------------------------------------

def test_entries_persist_across_store_instances(tmp_path: Path):
    p = tmp_path / "d.json"
    s1 = Store(path=p)
    s1.add("STEEL PROPERTY SL", label="EMPRESA")
    s2 = Store(path=p)
    assert [e.term for e in s2.all_entries()] == ["STEEL PROPERTY SL"]
    assert s2.analyze("STEEL PROPERTY SL")


def test_add_deduplicates_same_smart_term(store: Store):
    store.add("García", label="NOMBRE")
    store.add("garcia", label="NOMBRE")  # same folded target
    assert len(store.all_entries()) == 1


def test_remove(store: Store):
    e = store.add("X", label="OTRO")
    assert store.remove(e.id) is True
    assert store.all_entries() == []
    assert store.remove("nonexistent") is False


# --- import / smart merge --------------------------------------------------

def test_import_merges_without_duplicates_keeping_local(store: Store):
    store.add("García", label="NOMBRE")
    result = store.import_terms([
        {"term": "garcia", "label": "OTRO"},          # duplicate → skipped
        {"term": "STEEL PROPERTY SL", "label": "EMPRESA"},  # new → added
    ])
    assert result == {"added": 1, "skipped": 1, "invalid": 0}
    terms = {e.term for e in store.all_entries()}
    assert terms == {"García", "STEEL PROPERTY SL"}
    # Local label was kept, not overwritten by the import.
    garcia = next(e for e in store.all_entries() if e.term == "García")
    assert garcia.label == "NOMBRE"


def test_import_counts_invalid(store: Store):
    result = store.import_terms([
        {"term": "", "label": "X"},               # empty
        {"term": "(bad", "match": "regex"},       # invalid regex
        "not a dict",                              # wrong type
        {"term": "OK", "label": "OTRO"},
    ])
    assert result["added"] == 1
    assert result["invalid"] == 3


def test_export_roundtrips_through_import(tmp_path: Path):
    s1 = Store(path=tmp_path / "a.json")
    s1.add("García", label="NOMBRE")
    s1.add("STEEL PROPERTY SL", label="EMPRESA", match="smart")
    exported = s1.export_terms()

    s2 = Store(path=tmp_path / "b.json")
    res = s2.import_terms(exported)
    assert res["added"] == 2
    assert {e.term for e in s2.all_entries()} == {"García", "STEEL PROPERTY SL"}


# --- labels ----------------------------------------------------------------

def test_normalize_label():
    assert normalize_label("empresa") == "EMPRESA"
    assert normalize_label("Razón Social") == "RAZÓN_SOCIAL"
    assert normalize_label("  ") == "OTRO"
    assert normalize_label("a-b c") == "A_B_C"


# --- pipeline integration --------------------------------------------------

def test_pipeline_uses_custom_dictionary(tmp_path, monkeypatch):
    """A dictionary term is redacted even though no model/recognizer flags it."""
    from server import pipeline
    from opf._api import RedactionResult

    # Point the module-global store at a temp file and seed it.
    s = Store(path=tmp_path / "d.json")
    s.add("STEEL PROPERTY SL", label="EMPRESA")
    monkeypatch.setattr(custom_dict, "get_store", lambda: s)
    # No spaCy noise, no opf spans.
    monkeypatch.setattr(pipeline.ner_es, "analyze", lambda text, *, strict: [])

    text = "Comparece STEEL PROPERTY SL en el acto."
    stub = RedactionResult(
        schema_version="1", summary={}, text=text,
        detected_spans=(), redacted_text=text, warning=None,
    )
    result = pipeline.merge_and_redact(text, stub, mode="balanced")
    assert "STEEL PROPERTY SL" not in result.redacted_text
    assert "[EMPRESA_1]" in result.redacted_text


def test_pipeline_custom_dict_bypasses_false_positive_filter(tmp_path, monkeypatch):
    """A lowercase common word added by the user is still redacted.

    The FP filter would normally reject a bare lowercase token; dictionary
    entries are deterministic and must bypass it.
    """
    from server import pipeline
    from opf._api import RedactionResult

    s = Store(path=tmp_path / "d.json")
    s.add("proyecto fénix", label="OTRO")
    monkeypatch.setattr(custom_dict, "get_store", lambda: s)
    monkeypatch.setattr(pipeline.ner_es, "analyze", lambda text, *, strict: [])

    text = "El proyecto fénix es confidencial."
    stub = RedactionResult(
        schema_version="1", summary={}, text=text,
        detected_spans=(), redacted_text=text, warning=None,
    )
    result = pipeline.merge_and_redact(text, stub, mode="balanced")
    assert "proyecto fénix" not in result.redacted_text
    assert "[OTRO_1]" in result.redacted_text


# --- API endpoints ---------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    s = Store(path=tmp_path / "api_terms.json")
    monkeypatch.setattr(custom_dict, "get_store", lambda: s)
    from server.main import app
    try:
        return TestClient(app)
    except TypeError as exc:
        pytest.skip(f"TestClient unavailable: {exc}")


def test_api_list_empty(client):
    r = client.get("/api/dictionary")
    assert r.status_code == 200
    body = r.json()
    assert body["terms"] == []
    assert "EMPRESA" in body["labels"]
    assert "smart" in body["match_modes"]


def test_api_add_list_delete(client):
    r = client.post("/api/dictionary",
                    json={"term": "STEEL PROPERTY SL", "label": "EMPRESA"})
    assert r.status_code == 200
    entry_id = r.json()["id"]

    r = client.get("/api/dictionary")
    assert [e["term"] for e in r.json()["terms"]] == ["STEEL PROPERTY SL"]

    r = client.delete(f"/api/dictionary/{entry_id}")
    assert r.status_code == 200
    assert client.get("/api/dictionary").json()["terms"] == []


def test_api_add_rejects_invalid_regex(client):
    r = client.post("/api/dictionary",
                    json={"term": "(bad", "match": "regex"})
    assert r.status_code == 400


def test_api_update(client):
    entry_id = client.post(
        "/api/dictionary", json={"term": "García", "label": "NOMBRE"}
    ).json()["id"]
    r = client.put(f"/api/dictionary/{entry_id}", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_api_update_missing_404(client):
    r = client.put("/api/dictionary/nope", json={"enabled": False})
    assert r.status_code == 404


def test_api_import_and_export_roundtrip(client):
    client.post("/api/dictionary", json={"term": "García", "label": "NOMBRE"})
    r = client.post("/api/dictionary/import", json={"terms": [
        {"term": "garcia", "label": "OTRO"},                 # dup → skipped
        {"term": "STEEL PROPERTY SL", "label": "EMPRESA"},   # new → added
    ]})
    assert r.json() == {"added": 1, "skipped": 1, "invalid": 0}

    r = client.get("/api/dictionary/export")
    assert r.status_code == 200
    exported = r.json()
    assert {t["term"] for t in exported["terms"]} == {"García", "STEEL PROPERTY SL"}
