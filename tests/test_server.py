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
