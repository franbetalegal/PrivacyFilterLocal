"""Backend API tests (model-free paths) using FastAPI's TestClient.

These cover routing, validation and serialization without loading the model.
If the installed httpx/starlette combination cannot build a TestClient, the
whole module is skipped (the assertions still run on a clean install where
requirements-server.txt pins a compatible stack).
"""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from server.main import app

try:
    client = TestClient(app)
except TypeError as exc:  # httpx/starlette version mismatch in this env
    pytest.skip(f"TestClient unavailable: {exc}", allow_module_level=True)


def test_version():
    r = client.get("/api/version")
    assert r.status_code == 200
    assert "version" in r.json()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "model_loaded" in r.json()


def test_redact_empty_text_is_noop():
    r = client.post("/api/redact", json={"text": "   "})
    assert r.status_code == 200
    body = r.json()
    assert body["empty"] is True
    assert body["detected_spans"] == []


def test_redact_file_rejects_unsupported_type():
    files = {"file": ("note.xyz", io.BytesIO(b"hello"), "application/octet-stream")}
    r = client.post("/api/redact-file", files=files)
    assert r.status_code == 400


def test_download_unknown_token_404():
    r = client.get("/api/download/does-not-exist")
    assert r.status_code == 404


def test_redact_empty_text_echoes_mode():
    """Mode is echoed even for empty input so the UI knows which preset ran."""
    r = client.post("/api/redact", json={"text": "  ", "mode": "conservative"})
    assert r.status_code == 200
    body = r.json()
    assert body["empty"] is True
    assert body["mode"] == "conservative"


def test_redact_empty_text_defaults_mode_to_balanced():
    r = client.post("/api/redact", json={"text": " "})
    assert r.status_code == 200
    assert r.json()["mode"] == "balanced"


def test_apply_rejects_unsupported_type():
    """/api/redact-file/apply only accepts PDF and DOCX."""
    files = {"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")}
    r = client.post(
        "/api/redact-file/apply",
        files=files,
        data={"spans_json": "[]"},
    )
    assert r.status_code == 400


def test_apply_rejects_malformed_spans_json():
    files = {"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
    r = client.post(
        "/api/redact-file/apply",
        files=files,
        data={"spans_json": "{not-json"},
    )
    assert r.status_code == 400


def test_apply_docx_with_empty_span_list(tmp_path):
    """Empty span list must succeed and return a fresh download token."""
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("Some benign text without PII.")
    p = tmp_path / "clean.docx"
    doc.save(str(p))
    files = {"file": ("clean.docx", p.read_bytes(),
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    r = client.post(
        "/api/redact-file/apply",
        files=files,
        data={"spans_json": "[]"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied_span_count"] == 0
    assert body["download_token"]
    assert body["download_name"] == "clean_ANONIMIZED.docx"


def test_apply_docx_with_curated_span_list(tmp_path):
    """A user-curated span list is applied verbatim (no re-detection)."""
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("El testigo Juan García firmó el acta.")
    p = tmp_path / "in.docx"
    doc.save(str(p))
    files = {"file": ("in.docx", p.read_bytes(),
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    spans = [{
        "start": 11, "end": 22, "text": "Juan García",
        "placeholder": "[NOMBRE_1]", "label": "NOMBRE",
    }]
    r = client.post(
        "/api/redact-file/apply",
        files=files,
        data={"spans_json": __import__("json").dumps(spans)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied_span_count"] == 1
    assert body["download_token"]

    # Fetch the redacted DOCX and verify the name is gone.
    download = client.get(f"/api/download/{body['download_token']}")
    assert download.status_code == 200
    out = tmp_path / "out.docx"
    out.write_bytes(download.content)
    from docx import Document
    text = "\n".join(x.text for x in Document(str(out)).paragraphs)
    assert "Juan García" not in text
    assert "[NOMBRE_1]" in text
