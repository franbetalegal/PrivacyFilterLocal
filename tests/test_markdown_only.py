"""Tests for the standalone Markdown conversion endpoint.

The endpoint at ``POST /api/markdown`` lets a user convert a document to
Markdown *without* running PII detection. That is deliberately dangerous (the
output can leak PII), so the tests focus on the contract: format gating,
happy-path conversion, and the "empty output → error" branch that keeps a
scanned PDF from silently producing an unusable ``.md``.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("fastapi")
pytest.importorskip("markitdown")
from fastapi.testclient import TestClient

from server import markdown_export
from server.main import app
from server import messages

try:
    client = TestClient(app)
except TypeError as exc:
    pytest.skip(f"TestClient unavailable: {exc}", allow_module_level=True)


def _upload(name: str, data: bytes, content_type: str = "application/octet-stream"):
    return client.post(
        "/api/markdown",
        files={"file": (name, io.BytesIO(data), content_type)},
    )


def test_rejects_unsupported_extension():
    r = _upload("evil.exe", b"MZ\x90\x00")
    assert r.status_code == 400
    # The backend answers with a message code; the Spanish sentence lives in
    # the frontend (see server/messages.py).
    detail = r.json()["detail"]
    assert detail["code"] == messages.MARKDOWN_UNSUPPORTED_FORMAT
    assert detail["params"]["ext"] == ".exe"


def test_txt_roundtrip_returns_download_token(monkeypatch):
    """A .txt with real content converts, produces a token and char_count."""
    # Bypass markitdown for a hermetic test: the endpoint only cares about the
    # string it gets back. This still exercises the file plumbing, the token
    # registration and the response shape end-to-end.
    monkeypatch.setattr(
        markdown_export, "to_markdown",
        lambda path, ext, **kw: "# Hola\n\nContenido de ejemplo.",
    )
    r = _upload("nota.txt", b"Hola mundo.\n", content_type="text/plain")
    assert r.status_code == 200
    body = r.json()
    assert body["download_name"] == "nota.md"
    assert body["download_token"]
    assert body["char_count"] > 0
    # And the token is actually downloadable (once).
    dl = client.get(f"/api/download/{body['download_token']}")
    assert dl.status_code == 200
    assert "Hola" in dl.text


def test_empty_conversion_is_reported_as_422(monkeypatch):
    """Scanned PDFs without a text layer must not produce a blank .md silently."""
    monkeypatch.setattr(markdown_export, "to_markdown", lambda *a, **kw: "")
    r = _upload("scan.pdf", b"%PDF-1.4\n%\xE2\xE3\xCF\xD3\n", content_type="application/pdf")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == messages.MARKDOWN_NO_TEXT


def test_accepts_all_supported_extensions(monkeypatch):
    """Sanity check on the extension allow-list; every listed ext must pass."""
    monkeypatch.setattr(markdown_export, "to_markdown", lambda *a, **kw: "x")
    for ext in (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".csv", ".md"):
        r = _upload(f"file{ext}", b"data")
        assert r.status_code == 200, f"{ext} should be accepted: {r.text}"
