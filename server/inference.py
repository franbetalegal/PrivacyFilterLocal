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

# Viterbi calibration presets — each file shifts the CRF decoder's
# transition biases so opf emits fewer/tighter spans (conservative) or more/
# broader spans (aggressive). See server/viterbi_presets/*.json.
_PRESETS_DIR = Path(__file__).resolve().parent / "viterbi_presets"
_MODE_TO_CALIBRATION: dict[str, Path] = {
    "conservative": _PRESETS_DIR / "conservative.json",
    "balanced":     _PRESETS_DIR / "balanced.json",
    "aggressive":   _PRESETS_DIR / "aggressive.json",
}
DEFAULT_MODE = "balanced"


def calibration_path_for(mode: str) -> Optional[str]:
    """Return the absolute path to the Viterbi calibration file for ``mode``.

    ``None`` if the mode is unknown or the file is missing on disk (which
    would fall back to the checkpoint's built-in calibration).
    """
    path = _MODE_TO_CALIBRATION.get(mode)
    if path is None or not path.is_file():
        return None
    return str(path)

# Lightweight state for the /api/health endpoint.
_state = {"loaded": False, "loading": False, "device": None}

# First-run model download progress (surfaced in the web UI instead of a console).
_dl_state = {"downloading": False, "pct": 0, "error": None}

# Same, for the spaCy NER models. Kept separate from _dl_state because the two
# downloads are independent artifacts with very different sizes, and the UI
# names which one it is waiting for.
#
# ``latest`` caches what the startup check learned about newer published
# versions, so /api/health can answer that question without a network call per
# poll.
_ner_state: dict = {
    "installing": False,
    "pct": 0,
    "current": None,
    "error": None,
    "latest": {},
}


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

            _configure_torch_threads()
            from server.device import detect_device, describe
            device = detect_device()
            # opf's MoE defaults to Triton kernels on any non-CPU device, but
            # Triton is CUDA-only *and* an optional dependency. Without this,
            # a CUDA box with no triton installed — or any Apple Silicon Mac —
            # dies at the first forward pass with ModuleNotFoundError.
            # See privacy-filter/opf/_model/model.py:754.
            triton_ok = device == "cuda" and _triton_available()
            if device != "cpu" and not triton_ok:
                os.environ.setdefault("OPF_MOE_TRITON", "0")
                if device == "cuda":
                    logger.warning(
                        "CUDA detected but 'triton' is not installed, so opf's "
                        "fast GPU MoE kernels are unavailable. Falling back to "
                        "the portable MoE path. Install triton for full GPU "
                        "throughput."
                    )
            # Swap in the expert-grouped MoE unless Triton is doing the job.
            # It self-calibrates per host (see server/moe_fast.py), so this is
            # correct on ARM, Intel and AMD rather than tuned for one of them.
            if not triton_ok:
                from server import moe_fast
                moe_fast.install()
            logger.info("Loading Privacy Filter model on %s ...", describe(device))
            try:
                _model = OPF(device=device)
            except Exception as exc:  # noqa: BLE001
                if device != "cpu":
                    logger.warning(
                        "Model load failed on %s (%s); falling back to CPU.",
                        device, exc,
                    )
                    device = "cpu"
                    _model = OPF(device="cpu")
                else:
                    raise
            _state["device"] = device
            logger.info("Model loaded on %s", describe(device))
            _state["loaded"] = True
            return _model
        finally:
            _state["loading"] = False


def _triton_available() -> bool:
    """True if opf's Triton MoE kernels can actually be imported."""
    try:
        import triton  # noqa: F401
    except Exception:  # noqa: BLE001 — a broken install must not be fatal
        return False
    return True


def _configure_torch_threads() -> None:
    """Cap torch's CPU intra-op parallelism to the shared core budget.

    Model inference on CPU is the heaviest single stage; letting torch use the
    reserved-core-aware budget (rather than every core) keeps the host
    responsive while still using the cores we're allowed. Best-effort: torch
    only honors ``set_num_threads`` before the first parallel op runs, which is
    why this is called just before the model is constructed.
    """
    from server import concurrency

    try:
        import torch

        target = concurrency.worker_count()
        torch.set_num_threads(target)
        actual = torch.get_num_threads()
        inter = torch.get_num_interop_threads()
        logger.info(
            "torch threads: requested=%d intra=%d interop=%d",
            target, actual, inter,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not set torch thread count: %s", exc)


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


def _ner_progress(name: str, message: str, fraction: float, index: int, total: int) -> None:
    """Turn one model's 0..1 progress into a percentage across all of them.

    Logged only when the whole percent changes: the downloader reports every
    512 KB block, which on a 600 MB model is over a thousand identical lines in
    the file the support bundle ships.
    """
    share = (index + max(0.0, min(1.0, fraction))) / max(1, total)
    pct = int(round(share * 100))
    changed = pct != _ner_state["pct"]
    _ner_state["pct"] = pct
    _ner_state["current"] = name
    if changed:
        logger.info("%s (%d%%)", message, pct)


def ensure_ner_models_ready() -> None:
    """Blocking: install every configured spaCy model that is missing.

    This is what keeps the app from anonymising with the name detector absent.
    Every release up to 2.6.3 shipped without these models — no launcher, build
    script or CI job installed them — so documents came back with every name
    written in caps untouched while DNI and addresses were redacted, which
    reads as a working anonymisation and is not one.

    Safe to call when everything is present (no-op). Failures are recorded in
    ``_ner_state["error"]`` rather than raised: the caller is the startup
    preflight and the UI reports the state.
    """
    from server import ner_models

    pending = ner_models.missing()
    if not pending:
        return
    logger.info("NER models missing, installing: %s", ", ".join(pending))
    _ner_state["installing"] = True
    _ner_state["error"] = None
    _ner_state["pct"] = 0
    failures: list[str] = []
    try:
        for index, name in enumerate(pending):
            ok, message = ner_models.install(
                name,
                progress_callback=lambda msg, frac, n=name, i=index: _ner_progress(
                    n, msg, frac, i, len(pending)
                ),
            )
            if not ok:
                failures.append(message)
        if failures:
            _ner_state["error"] = " ".join(failures)
        else:
            _ner_state["pct"] = 100
    finally:
        _ner_state["installing"] = False
        _ner_state["current"] = None


def refresh_ner_models() -> None:
    """Install a newer compatible model version when one has been published.

    Runs on every start, after the models are known to be present, and never
    blocks availability: not knowing about a new version is not a reason to
    keep anyone waiting. An install is atomic and validated before it replaces
    anything (see :func:`server.ner_models.install`), and the loaded pipelines
    are dropped afterwards so the running process picks the new version up.
    """
    from server import ner_es, ner_models

    try:
        table = ner_models._compatibility_table()
    except Exception as exc:  # noqa: BLE001 — offline is a normal state here
        logger.info("Skipping the NER model version check: %s", exc)
        return

    upgraded = False
    for name in ner_models.MODEL_NAMES:
        latest = ner_models.latest_compatible(name, table=table)
        if latest:
            _ner_state["latest"][name] = latest
        # Only the copy this app manages is upgraded. A pip-installed model
        # belongs to whoever created the virtualenv (a development machine),
        # and silently shadowing it with a download would be a surprise.
        if not ner_models.is_installed(name):
            continue
        current = ner_models.installed_version(name)
        if not latest or not current or latest == current:
            continue
        logger.info("Newer NER model published: %s %s -> %s", name, current, latest)
        ok, message = ner_models.install(name, version=latest)
        if ok:
            upgraded = True
        else:
            logger.warning("Keeping %s %s: %s", name, current, message)
    if upgraded:
        ner_es.reload()


def run_preflight() -> None:
    """Blocking: get every detection component in place, in order.

    Order matters: the opf checkpoint first because it is the larger download
    and the one the UI already explains, then the NER models, then the version
    check. All three report into the state the health endpoint serves.
    """
    ensure_model_ready()
    ensure_ner_models_ready()
    refresh_ner_models()


def ner_status() -> dict:
    """NER component state for /api/health and the diagnostics bundle."""
    from server import ner_es, ner_models

    models = ner_models.status()
    for entry in models:
        entry["latest"] = _ner_state["latest"].get(entry["name"])
    return {
        "installing": _ner_state["installing"],
        "install_pct": _ner_state["pct"],
        "current": _ner_state["current"],
        "error": _ner_state["error"],
        # Presence, not "already loaded": the pipelines load lazily on the
        # first document and health must not pay for a 1.2 GB load to answer.
        "available": not ner_models.missing(),
        "loaded": ner_es.loaded_model_count(),
        "models": models,
    }


def start_background_download() -> None:
    """Kick off the first-run preflight without blocking startup.

    The server listens immediately; /api/health reports the progress and the UI
    holds the "Preparing" screen until every component is ready.
    """
    loop = asyncio.get_running_loop()
    loop.run_in_executor(_executor, run_preflight)


def ocr_status() -> dict:
    """Whether scanned pages can be read at all.

    Not used to gate anything today, but a missing Tesseract makes a scanned
    PDF extract as empty text, and "no detections" then looks exactly like a
    clean document. Reporting it beats leaving it in the log.
    """
    import shutil

    try:
        import pytesseract  # noqa: F401
        library = True
    except ImportError:
        library = False
    binary = shutil.which("tesseract")
    return {"available": bool(library and binary), "binary": binary}


def components() -> dict:
    """State of every detection component, for health and diagnostics.

    One place that answers "is this install complete?". The bug this exists for
    was invisible for three releases precisely because nothing ever asked.
    """
    ner = ner_status()
    return {
        "opf": {
            "available": _checkpoint_is_valid(checkpoint_dir()),
            "loaded": _state["loaded"],
            "downloading": _dl_state["downloading"],
            "download_pct": _dl_state["pct"],
            "error": _dl_state["error"],
            "checkpoint_dir": str(checkpoint_dir()),
        },
        "ner": ner,
        "ocr": ocr_status(),
    }


def status() -> dict:
    """Return a snapshot of the model/download state for the health endpoint."""
    from server.device import describe, detect_device

    device = _state["device"] or detect_device()
    parts = components()
    # ``ready`` is decided here rather than in the browser so there is a single
    # definition of "this app can anonymise properly right now". OCR is not in
    # it: a missing Tesseract costs scanned documents, not names.
    ready = (
        parts["opf"]["available"]
        and not parts["opf"]["downloading"]
        and parts["ner"]["available"]
        and not parts["ner"]["installing"]
        and parts["opf"]["error"] is None
        and parts["ner"]["error"] is None
    )
    return {
        "model_loaded": _state["loaded"],
        "loading": _state["loading"],
        "downloading": _dl_state["downloading"],
        "download_pct": _dl_state["pct"],
        "error": _dl_state["error"],
        "device": device,
        "device_label": describe(device),
        "ready": ready,
        "components": parts,
    }


# Chunking thresholds. Empirical measurement (see docs) shows opf's forward
# pass is *linear* in token count on CPU — chunking a 5k-token doc into 8
# pieces gives no speedup and adds ~5% overhead from the extra overlap
# tokens. So the default threshold is high enough that typical legal
# documents (up to ~15k tokens) take the single-pass route. Chunking still
# activates automatically for the very-long-document tail: it keeps peak
# memory bounded and stays within opf's 128k-token context. Both knobs are
# env-var tunable for benchmarking.
_CHUNK_CHARS = int(os.environ.get("PF_OPF_CHUNK_CHARS", "50000"))
_CHUNK_OVERLAP = int(os.environ.get("PF_OPF_CHUNK_OVERLAP", "1500"))


def _opf_redact_chunked(model, text: str, decode_options):
    """Run ``opf.redact`` on overlapping chunks and merge the spans.

    Returns a ``RedactionResult`` whose ``detected_spans`` are absolute-offset
    into the original ``text`` and deduplicated across chunk overlaps.
    """
    import time as _t
    from opf._api import RedactionResult
    from opf._core.runtime import DetectedSpan

    from server.chunking import split_with_overlap

    chunks = split_with_overlap(text, _CHUNK_CHARS, _CHUNK_OVERLAP)
    if len(chunks) <= 1:
        return model.redact(text, decode=decode_options)

    merged: list[DetectedSpan] = []
    seen: set[tuple[int, int, str]] = set()
    warning: str | None = None
    per_chunk_times: list[float] = []
    for ch in chunks:
        t = _t.time()
        r = model.redact(ch.text, decode=decode_options)
        per_chunk_times.append(_t.time() - t)
        if r.warning and warning is None:
            warning = r.warning
        for span in r.detected_spans:
            abs_start = ch.offset + span.start
            abs_end = ch.offset + span.end
            key = (abs_start, abs_end, span.label)
            if key in seen:
                continue
            seen.add(key)
            merged.append(DetectedSpan(
                label=span.label,
                start=abs_start,
                end=abs_end,
                text=span.text,
                placeholder=span.placeholder,
            ))
    merged.sort(key=lambda s: (s.start, s.end))
    logger.info(
        "opf chunking: %d chunks, per-chunk secs=[%s]",
        len(chunks),
        ", ".join(f"{t:.2f}" for t in per_chunk_times),
    )
    by_label: dict[str, int] = {}
    for s in merged:
        by_label[s.label] = by_label.get(s.label, 0) + 1
    summary = {
        "span_count": len(merged),
        "by_label": dict(sorted(by_label.items())),
    }
    return RedactionResult(
        schema_version=1,
        summary=summary,
        text=text,
        detected_spans=tuple(merged),
        redacted_text=text,  # pipeline will rebuild the redacted text
        warning=warning,
    )


def _redact_sync(text: str, mode: str = DEFAULT_MODE):
    """Blocking redaction used inside the inference thread pool.

    Runs opf with the Viterbi calibration selected by ``mode`` — chunked over
    the input for long texts, single-pass for short ones — then delegates to
    :mod:`server.pipeline` for the deterministic + NER layers and the
    consistent pseudonymisation.
    """
    import time as _t
    from opf._api import DecodeOptions
    from server import pipeline

    model = get_model()
    calib = calibration_path_for(mode)
    decode_options = (
        DecodeOptions(viterbi_calibration_path=calib) if calib is not None else None
    )

    t0 = _t.time()
    opf_result = _opf_redact_chunked(model, text, decode_options)
    t_opf = _t.time() - t0
    t1 = _t.time()
    result = pipeline.merge_and_redact(text, opf_result, mode=mode)
    t_pipeline = _t.time() - t1
    logger.info(
        "TIMING opf.redact=%.2fs pipeline.merge=%.2fs text=%d chars mode=%s",
        t_opf, t_pipeline, len(text), mode,
    )
    return result


async def redact(text: str, mode: str = DEFAULT_MODE):
    """Run redaction off the event loop, serialized via the single-worker pool.

    ``mode`` picks the precision/recall operating point (``conservative``,
    ``balanced``, ``aggressive``); unknown values fall back to ``balanced``.
    Returns the ``RedactionResult`` from ``OPF.redact`` (post-pipeline).
    """
    if mode not in _MODE_TO_CALIBRATION:
        mode = DEFAULT_MODE
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _redact_sync, text, mode)


async def run_blocking(func: Callable, *args):
    """Run an arbitrary blocking callable on the inference thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, func, *args)
