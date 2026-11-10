"""Phase 1 robustness: warm-up gating, cancellation, temp hygiene, budgets.

Each test pins down a failure that reached an end user: the first document
killing the process, an abandoned job holding the single worker, raw originals
left in the temp folder after a crash, and an OCR pool too big for the RAM.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import concurrency, downloads, inference, jobs, vocab


# --- warm-up gates "ready" -------------------------------------------------


@pytest.fixture
def warm_state(monkeypatch):
    state = {"warming": False, "warm": False, "error": None}
    monkeypatch.setattr(inference, "_warm_state", state)
    return state


def _everything_downloaded(monkeypatch):
    monkeypatch.setattr(inference, "_checkpoint_is_valid", lambda _p: True)
    monkeypatch.setattr(inference, "_dl_state",
                        {"downloading": False, "pct": 100, "error": None})
    monkeypatch.setattr(inference, "ner_status", lambda: {
        "installing": False, "install_pct": 100, "current": None,
        "error": None, "available": True, "loaded": 0, "models": [],
    })


def test_not_ready_until_the_models_are_actually_loaded(monkeypatch, warm_state):
    """Files on disk used to count as ready; the first document then paid for
    the 2.8 GB load inside the user's request and could die doing it."""
    _everything_downloaded(monkeypatch)
    assert inference.status()["ready"] is False

    warm_state["warm"] = True
    assert inference.status()["ready"] is True


def test_warm_up_failure_is_reported_not_raised(monkeypatch, warm_state):
    monkeypatch.setattr(inference, "_dl_state",
                        {"downloading": False, "pct": 100, "error": None})
    monkeypatch.setattr(inference, "_ner_state", {
        "installing": False, "pct": 100, "current": None, "error": None,
        "latest": {},
    })
    monkeypatch.setattr(vocab, "ensure_present", lambda: None)

    def boom(_text, _mode):
        raise RuntimeError("model file is truncated")

    monkeypatch.setattr(inference, "_redact_sync", boom)
    inference.warm_up()

    assert warm_state["warm"] is False
    assert warm_state["warming"] is False
    assert "truncated" in warm_state["error"]


def test_warm_up_runs_a_real_redaction(monkeypatch, warm_state):
    monkeypatch.setattr(inference, "_dl_state",
                        {"downloading": False, "pct": 100, "error": None})
    monkeypatch.setattr(inference, "_ner_state", {
        "installing": False, "pct": 100, "current": None, "error": None,
        "latest": {},
    })
    monkeypatch.setattr(vocab, "ensure_present", lambda: None)
    calls = []
    monkeypatch.setattr(inference, "_redact_sync",
                        lambda text, mode: calls.append((text, mode)))
    inference.warm_up()

    assert warm_state["warm"] is True
    assert warm_state["error"] is None
    assert calls and calls[0][0] == inference._WARMUP_TEXT


def test_warm_up_waits_for_downloads_to_succeed(monkeypatch, warm_state):
    monkeypatch.setattr(inference, "_dl_state",
                        {"downloading": False, "pct": 0, "error": "offline"})
    monkeypatch.setattr(inference, "_redact_sync",
                        lambda *_: pytest.fail("must not load a missing model"))
    inference.warm_up()
    assert warm_state["warm"] is False


# --- cancellation ----------------------------------------------------------


def test_check_is_a_no_op_outside_a_bound_job():
    jobs.bind(None)
    jobs.check()


def test_a_job_cancelled_while_queued_never_starts():
    event = threading.Event()
    event.set()
    ran = []
    with pytest.raises(jobs.Cancelled):
        inference._bound(event, lambda: ran.append(1))
    assert ran == []
    jobs.check()  # binding was released


def test_cancelled_survives_broad_exception_handlers():
    """Extraction is full of ``except Exception`` fallbacks; none may eat it."""
    event = threading.Event()
    event.set()
    jobs.bind(event)
    try:
        with pytest.raises(jobs.Cancelled):
            try:
                jobs.check()
            except Exception:  # noqa: BLE001
                pytest.fail("Cancelled was swallowed by except Exception")
    finally:
        jobs.bind(None)


def test_chunked_inference_stops_between_chunks(monkeypatch):
    from server import chunking

    event = threading.Event()
    seen = []

    class _Model:
        def redact(self, text, decode=None):
            seen.append(text)
            event.set()  # the user cancels while the first chunk runs
            from opf._api import RedactionResult
            return RedactionResult(schema_version=1, summary={}, text=text,
                                   detected_spans=(), redacted_text=text,
                                   warning=None)

    monkeypatch.setattr(inference, "_CHUNK_CHARS", 100)
    monkeypatch.setattr(inference, "_CHUNK_OVERLAP", 10)
    text = "palabra " * 200
    assert len(chunking.split_with_overlap(text, 100, 10)) > 2

    with pytest.raises(jobs.Cancelled):
        inference._bound(event, inference._opf_redact_chunked, _Model(), text, None)
    assert len(seen) == 1


def test_route_turns_a_cancelled_job_into_a_cancelled_response(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from server.main import app

    async def cancelled(*_a, **_k):
        raise jobs.Cancelled()

    monkeypatch.setattr(inference, "redact", cancelled)
    r = TestClient(app).post("/api/redact", json={"text": "Hola Ana"})
    assert r.status_code == 200
    assert r.json() == {"cancelled": True}


# --- temp-file hygiene ----------------------------------------------------


def test_stale_originals_from_a_crashed_run_are_removed(tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    old = time.time() - 3600
    stale = tmp_path / "upload_abc.pdf"
    fresh = tmp_path / "upload_now.pdf"
    foreign = tmp_path / "someone_else.pdf"
    for f in (stale, fresh, foreign):
        f.write_bytes(b"x")
    os.utime(stale, (old, old))
    os.utime(foreign, (old, old))

    assert downloads.sweep_stale_temp_files() == 1
    assert not stale.exists()
    assert fresh.exists(), "an in-flight upload of another instance was deleted"
    assert foreign.exists(), "a file that is not ours was deleted"


# --- OCR worker budget -----------------------------------------------------


def test_ocr_workers_are_capped_by_available_memory(monkeypatch):
    monkeypatch.setattr(concurrency, "worker_count", lambda: 6)
    gb = 1024 ** 3
    monkeypatch.setattr(concurrency, "available_memory_bytes", lambda: 3 * gb)
    assert concurrency.ocr_worker_count() == 2

    monkeypatch.setattr(concurrency, "available_memory_bytes", lambda: gb)
    assert concurrency.ocr_worker_count() == 1

    monkeypatch.setattr(concurrency, "available_memory_bytes", lambda: None)
    assert concurrency.ocr_worker_count() == 6


# --- tokenizer vocabulary --------------------------------------------------


def test_vocab_is_written_where_tiktoken_reads_it(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    payload = b"fake vocabulary"
    monkeypatch.setattr(vocab, "SHA256", hashlib.sha256(payload).hexdigest())

    class _Resp:
        content = payload

        def raise_for_status(self):
            pass

    calls = []

    def fake_get(url, timeout):
        calls.append(timeout)
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    vocab.ensure_present()
    expected = tmp_path / hashlib.sha1(vocab.URL.encode()).hexdigest()
    assert expected.read_bytes() == payload
    assert calls == [vocab.TIMEOUT_SECONDS], "the fetch must carry a timeout"

    vocab.ensure_present()
    assert len(calls) == 1, "a present vocabulary must not be fetched again"


def test_vocab_with_a_bad_hash_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))

    class _Resp:
        content = b"tampered"

        def raise_for_status(self):
            pass

    import requests
    monkeypatch.setattr(requests, "get", lambda url, timeout: _Resp())
    with pytest.raises(ValueError):
        vocab.ensure_present()
    assert not any(tmp_path.iterdir())
