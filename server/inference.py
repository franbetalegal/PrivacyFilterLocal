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


def status() -> dict:
    """Return a snapshot of the model state for the health endpoint."""
    return {"model_loaded": _state["loaded"], "loading": _state["loading"]}


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
