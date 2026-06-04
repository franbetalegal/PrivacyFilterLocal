"""Model lifecycle and inference for the FastAPI backend.

The OPF model is a single in-memory PyTorch instance. Inference is CPU-bound and
blocking, so it runs in a dedicated single-worker thread pool (which also
serializes calls against the single model instance) and is awaited from the
async request handlers without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
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
    """Return the default OPF checkpoint directory."""
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


def get_model():
    """Return the singleton OPF model, loading (and recovering) it lazily.

    Thread-safe: concurrent first-time callers are serialized so the model is
    constructed exactly once. Mirrors the recovery logic of the old
    ``app_local.get_model``.
    """
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        _state["loading"] = True
        try:
            logger.info("Loading Privacy Filter model...")
            from opf._api import OPF

            try:
                _model = OPF(device="cpu")
                logger.info("Model loaded")
            except (FileNotFoundError, RuntimeError) as exc:
                # Missing/partial checkpoint is the common cause; try to
                # recover by downloading a fresh copy.
                message = str(exc)
                recoverable = (
                    "missing config.json" in message
                    or "no .safetensors" in message
                    or "Checkpoint directory not found" in message
                )
                if not recoverable or not _is_partial_checkpoint(checkpoint_dir()):
                    logger.error("%s", exc)
                    raise

                logger.info("Checkpoint is missing or partial. Downloading...")
                _ensure_model_present(
                    progress_callback=lambda msg, pct: logger.info("%s", msg)
                )
                _model = OPF(device="cpu")
                logger.info("Model loaded after download")

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
