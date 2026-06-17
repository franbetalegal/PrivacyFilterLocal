"""Model lifecycle and inference for the FastAPI backend.

The OPF model is a single in-memory PyTorch instance. Inference is CPU-bound and
blocking, so it runs in a dedicated single-worker thread pool (which also
serializes calls against the single model instance) and is awaited from the
async request handlers without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("privacy_filter.inference")

_model = None
_model_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="opf-infer")

# Lightweight state for the /api/health endpoint.
_state = {"loaded": False, "loading": False}

# First-run model download progress (surfaced in the web UI instead of a console).
_dl_state = {"downloading": False, "pct": 0, "error": None}


def checkpoint_dir() -> Path:
    """Return the OPF checkpoint directory.

    Honors the OPF_CHECKPOINT environment variable (used by the portable build
    to keep the model inside its own folder); defaults to ~/.opf/privacy_filter.
    """
    override = os.environ.get("OPF_CHECKPOINT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".opf" / "privacy_filter"


def _is_partial_checkpoint(model_dir: Path) -> bool:
    """Return True if ``model_dir`` exists but is missing required files."""
    if not model_dir.exists() or not model_dir.is_dir():
        return False
    if not (model_dir / "config.json").is_file():
        return True
    return not any(model_dir.glob("*.safetensors"))


def _ensure_model_present(progress_callback: Optional[Callable] = None) -> None:
    """Download the OPF checkpoint if it is missing or in a partial state."""
    from model_update import download_model_update

    success, message = download_model_update(progress_callback=progress_callback)
    if not success:
        raise RuntimeError(
            f"Could not download the PII model: {message}. "
            f"Check your internet connection or remove the partial "
            f"checkpoint at {checkpoint_dir()} and try again."
        )


def _checkpoint_is_valid(model_dir: Path) -> bool:
    """Return True if ``model_dir`` looks like a complete OPF checkpoint."""
    return (
        model_dir.is_dir()
        and (model_dir / "config.json").is_file()
        and any(model_dir.glob("*.safetensors"))
    )


def get_model():
    """Return the singleton OPF model, downloading the checkpoint if needed.

    Thread-safe: concurrent first-time callers are serialized so the model is
    constructed exactly once.

    The checkpoint is ensured *before* constructing ``OPF``. This matters when
    ``OPF_CHECKPOINT`` is set (e.g. the portable build): in that case ``opf``
    points at the given directory and does NOT auto-download, so an empty/partial
    directory would only fail later at inference time. We download here instead.
    """
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        _state["loading"] = True
        try:
            from opf._api import OPF

            cp = checkpoint_dir()
            if not _checkpoint_is_valid(cp):
                logger.info("PII model not found at %s. Downloading...", cp)
                _ensure_model_present(
                    progress_callback=lambda msg, pct: logger.info("%s", msg)
                )

            logger.info("Loading Privacy Filter model...")
            _model = OPF(device="cpu")
            logger.info("Model loaded")
            _state["loaded"] = True
            return _model
        finally:
            _state["loading"] = False


def reset_model() -> None:
    """Drop the loaded model so the next call reloads it (e.g. after update)."""
    global _model
    with _model_lock:
        _model = None
        _state["loaded"] = False


def _download_progress(message, pct) -> None:
    """progress_callback for the model download; updates the web-visible state."""
    try:
        _dl_state["pct"] = max(0, min(100, int(round((pct or 0) * 100))))
    except (TypeError, ValueError):
        pass
    logger.info("%s (%d%%)", message, _dl_state["pct"])


def ensure_model_ready() -> None:
    """Blocking: make sure the checkpoint exists, downloading it if missing.

    Updates ``_dl_state`` so the web UI can show progress. Safe to call when the
    model is already present (no-op). Runs on the inference thread pool.
    """
    if _checkpoint_is_valid(checkpoint_dir()):
        return
    _dl_state["downloading"] = True
    _dl_state["error"] = None
    _dl_state["pct"] = 0
    try:
        _ensure_model_present(progress_callback=_download_progress)
        _dl_state["pct"] = 100
    except Exception as exc:  # noqa: BLE001
        _dl_state["error"] = str(exc)
        logger.error("Model download failed: %s", exc)
    finally:
        _dl_state["downloading"] = False


def start_background_download() -> None:
    """Kick off the first-run model download without blocking startup."""
    loop = asyncio.get_running_loop()
    loop.run_in_executor(_executor, ensure_model_ready)


def status() -> dict:
    """Return a snapshot of the model/download state for the health endpoint."""
    return {
        "model_loaded": _state["loaded"],
        "loading": _state["loading"],
        "downloading": _dl_state["downloading"],
        "download_pct": _dl_state["pct"],
        "error": _dl_state["error"],
    }


def _redact_sync(text: str):
    """Blocking redaction used inside the inference thread pool."""
    return get_model().redact(text)


async def redact(text: str):
    """Run redaction off the event loop, serialized via the single-worker pool.

    Returns the ``RedactionResult`` from ``OPF.redact``.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _redact_sync, text)


async def run_blocking(func: Callable, *args):
    """Run an arbitrary blocking callable on the inference thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, func, *args)
