"""FastAPI application for Privacy Filter - Local.

Serves the JSON API under ``/api`` and the built React frontend under ``/``.
Run with:  uvicorn server.main:app --host 0.0.0.0 --port 7860
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Importing the package configures sys.path for ``opf`` / update modules.
from server import PROJECT_DIR
from server import inference, redaction

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel


@dataclass(frozen=True)
class _SpanFromClient:
    """User-curated span sent to the /api/redact-file/apply endpoint.

    Deliberately duck-typed to :class:`opf._core.runtime.DetectedSpan` so it
    plugs into :func:`server.redaction.redact_pdf`/``redact_docx`` and
    :func:`server.verify.find_leaks` without adapters.
    """
    start: int
    end: int
    text: str
    placeholder: str
    label: str = ""


def _copy_to_temp(input_path: str, ext: str) -> str:
    """Copy ``input_path`` to a new temp file and return the new path.

    Used by the apply endpoint when the caller sends an empty span list — the
    file still needs a token-served copy so the download flow works.
    """
    out = redaction.temp_output_path(input_path, ext)
    shutil.copyfile(input_path, out)
    return out

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("privacy_filter")


def _setup_file_logging() -> Path | None:
    """Mirror logs to a rotating file so users can send diagnostics.

    The directory is PF_LOG_DIR (set by the portable launcher to stay inside the
    app folder) or ``<project>/logs`` by default.
    """
    log_dir = Path(os.environ.get("PF_LOG_DIR") or (PROJECT_DIR / "logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create log dir %s: %s", log_dir, exc)
        return None
    log_path = log_dir / "privacy-filter.log"
    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addHandler(handler)
    return log_path


_LOG_PATH = _setup_file_logging()

FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the first-run model download in the background so it doesn't block
    # the server from listening; progress is exposed via /api/health.
    try:
        inference.start_background_download()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not start background model download: %s", exc)
    yield


app = FastAPI(title="Privacy Filter - Local", version="2.0.0", lifespan=lifespan)

# Permissive CORS so the Vite dev server (different port) can call the API.
# In production the frontend is served from the same origin, so this is a no-op.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Log the full traceback and return a readable message to the UI."""
    ref = uuid.uuid4().hex[:8]
    logger.error(
        "Unhandled error [%s] on %s: %s",
        ref, request.url.path, exc, exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}", "ref": ref},
    )


# --------------------------------------------------------------------------
#  Schemas
# --------------------------------------------------------------------------
class RedactRequest(BaseModel):
    text: str
    # Precision/recall operating point. Unknown values fall back to "balanced"
    # in :func:`server.inference.redact` — no explicit validator here so the
    # API stays forward-compatible with future presets.
    mode: str = "balanced"


class DictEntryIn(BaseModel):
    """A dictionary term submitted from the UI (add or update)."""
    term: str
    label: str = "OTRO"
    match: str = "smart"
    enabled: bool = True


class DictEntryPatch(BaseModel):
    """Partial update — only the provided fields change."""
    term: str | None = None
    label: str | None = None
    match: str | None = None
    enabled: bool | None = None


class DictImportIn(BaseModel):
    """A dictionary export to smart-merge into the local one."""
    terms: list[dict]


class CaptureIn(BaseModel):
    """One reviewed example to append to the local gold set (Phase 7)."""
    text: str
    spans: list[dict]


class EvaluateIn(BaseModel):
    """Request to score the whole gold set in a given operating mode."""
    mode: str = "balanced"


# Accepted upload extensions (mirrors the old Gradio file_types list).
TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".xml", ".html"}
DOC_EXTS = {".pdf", ".docx"}

# token -> (path on disk, filename to present, created timestamp). Entries are
# removed when downloaded; a TTL sweep deletes ones never downloaded so redacted
# PII outputs never linger on disk.
_downloads: dict[str, tuple[str, str, float]] = {}
_downloads_lock = threading.Lock()
_DOWNLOAD_TTL_SECONDS = 1800  # 30 minutes


def _sweep_expired_downloads() -> None:
    """Delete redacted outputs that were never downloaded within the TTL."""
    now = time.time()
    expired = []
    with _downloads_lock:
        for token, (path, _name, created) in list(_downloads.items()):
            if now - created > _DOWNLOAD_TTL_SECONDS:
                expired.append(path)
                _downloads.pop(token, None)
    for path in expired:
        _safe_unlink(path)


def _register_download(path: str, download_name: str) -> str:
    token = uuid.uuid4().hex
    with _downloads_lock:
        _downloads[token] = (path, download_name, time.time())
    _sweep_expired_downloads()
    return token


def _pop_download(token: str) -> tuple[str, str] | None:
    with _downloads_lock:
        entry = _downloads.pop(token, None)
    if entry is None:
        return None
    path, name, _created = entry
    return (path, name)


def _safe_unlink(path: str | None) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as exc:
            logger.debug("Could not delete temp file %s: %s", path, exc)


def _bool_form(value: object) -> bool:
    """Interpret a multipart form field as a boolean.

    FastAPI's ``Form(...)`` gives us strings, and the frontend may send
    ``"true"``, ``"1"``, ``"on"`` or ``"yes"`` depending on the widget used.
    Same idea as :func:`str.lower` == "true", but tolerant.
    """
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _register_markdown_export(
    source_path: str,
    ext: str,
    *,
    original_filename: str,
    fallback_text: str | None,
) -> tuple[str | None, str | None]:
    """Produce the ``.md`` for the redacted output and expose it for download.

    Returns ``(token, name)`` on success or ``(None, None)`` if the conversion
    yielded no text (an unsupported format with no fallback, or an internal
    failure). Never raises: this is opt-in and secondary to the primary output,
    which is already downloadable at this point.
    """
    from server import markdown_export

    try:
        markdown = markdown_export.to_markdown(
            source_path, ext, fallback_text=fallback_text,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Markdown export failed for %s: %s", original_filename, exc)
        return None, None

    if not markdown.strip():
        return None, None

    stem = Path(original_filename).stem or "documento"
    md_name = f"{stem}_ANONIMIZED.md"
    fd, md_path = tempfile.mkstemp(suffix=".md", prefix="markdown_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(markdown)
    except OSError as exc:
        logger.warning("Could not write .md file %s: %s", md_path, exc)
        _safe_unlink(md_path)
        return None, None

    return _register_download(md_path, md_name), md_name


async def _fallback_redacted_text(
    in_path: str, ext: str, spans
) -> str | None:
    """Rebuild the redacted plain text for the scanned-PDF fallback in ``apply``.

    ``/api/redact-file`` has the pipeline's own ``RedactionResult.redacted_text``
    right there; ``/api/redact-file/apply`` doesn't (the pipeline never ran, the
    caller supplied the spans), so we re-extract the plain text and substitute
    each span. Extraction is cached by content, so this is a hit against the
    same request's earlier extraction whenever the user only tweaked spans.
    """
    if ext not in DOC_EXTS or not spans:
        return None
    try:
        if ext == ".pdf":
            text = await inference.run_blocking(
                redaction.extract_text_from_pdf, in_path
            )
        else:
            text = await inference.run_blocking(
                redaction.extract_text_from_docx, in_path
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not re-extract text for markdown fallback: %s", exc)
        return None
    if not text:
        return None
    # Substitute in reverse offset order so earlier substitutions don't shift
    # the offsets of later spans.
    result = text
    for span in sorted(spans, key=lambda s: s.start, reverse=True):
        result = result[: span.start] + span.placeholder + result[span.end :]
    return result


# --------------------------------------------------------------------------
#  API: core
# --------------------------------------------------------------------------
@app.get("/api/version")
def api_version() -> dict:
    """Return the local application version."""
    from app_update import get_local_version

    return {"version": get_local_version()}


@app.get("/api/health")
def api_health() -> dict:
    """Return model load state (for showing a loading indicator in the UI)."""
    return inference.status()


@app.post("/api/redact")
async def api_redact(req: RedactRequest) -> dict:
    """Detect and redact PII in a text string."""
    text = req.text or ""
    if not text.strip():
        return {"redacted_text": "", "detected_spans": [], "elapsed": 0.0,
                "empty": True, "mode": req.mode}
    start = time.time()
    result = await inference.redact(text, mode=req.mode)
    elapsed = time.time() - start
    payload = result.to_dict()
    payload["elapsed"] = round(elapsed, 3)
    payload["mode"] = req.mode
    return payload


@app.post("/api/redact-file")
async def api_redact_file(
    request: Request,
    file: UploadFile = File(...),
    mode: str = "balanced",
    also_markdown: str = Form("false"),
) -> dict:
    """Detect PII in an uploaded file.

    For PDF/DOCX a redacted copy is produced and exposed via a one-time
    ``download_token``. The uploaded input is always deleted after processing.
    If the client cancels (disconnects), the redaction step is skipped.

    When ``also_markdown`` is truthy, the *redacted* file is additionally
    converted to Markdown (for pasting into an LLM: fewer tokens than a PDF
    and explicit structure). A second one-time ``markdown_download_token`` is
    included in the response. The Markdown never sees the original document;
    it is produced from the redacted copy (and, for scanned PDFs whose
    redacted copy is images, from the already-substituted text the pipeline
    used).
    """
    t_total_start = time.time()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in TEXT_EXTS and ext not in DOC_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    # Persist the upload to a temp file (PyMuPDF/python-docx need a path).
    fd, in_path = tempfile.mkstemp(suffix=ext, prefix="upload_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(await file.read())

        t_extract_start = time.time()
        # Offload to the inference pool: extraction is CPU-bound and on a
        # scanned file runs for minutes, which would otherwise block the
        # asyncio loop — stalling /api/health polling and the cancellation
        # check below.
        if ext == ".pdf":
            text = await inference.run_blocking(
                redaction.extract_text_from_pdf, in_path)
            if text is None:
                raise HTTPException(status_code=422, detail="Could not read PDF.")
        elif ext == ".docx":
            text = await inference.run_blocking(
                redaction.extract_text_from_docx, in_path)
            if text is None:
                raise HTTPException(status_code=422, detail="Could not read DOCX.")
        else:
            text = Path(in_path).read_text(encoding="utf-8", errors="replace")
        t_extract = time.time() - t_extract_start
        logger.info(
            "TIMING extract=%.2fs (%d chars) file=%s",
            t_extract, len(text), Path(file.filename).name,
        )

        start = time.time()
        result = await inference.redact(text, mode=mode)
        t_detect = time.time() - start
        spans = result.detected_spans
        logger.info(
            "TIMING inference+pipeline=%.2fs spans=%d",
            t_detect, len(spans),
        )

        # If the user cancelled while we were detecting, skip the (possibly slow)
        # redaction step and don't create a download.
        if await request.is_disconnected():
            logger.info("redact-file cancelled by client; skipping redaction")
            return {"cancelled": True}

        download_token = None
        download_name = None
        markdown_token = None
        markdown_name = None
        leaked: list[str] = []
        # Zero-initialised so the timings dict is well-formed when this block
        # is skipped (plain-text upload, or a document with no detections).
        t_redact = 0.0
        t_verify = 0.0
        verified = False
        if spans and ext in DOC_EXTS:
            t_redact_start = time.time()
            if ext == ".pdf":
                out_path = await inference.run_blocking(
                    redaction.redact_pdf, in_path, spans
                )
            else:
                out_path = await inference.run_blocking(
                    redaction.redact_docx, in_path, spans
                )
            t_redact = time.time() - t_redact_start
            logger.info("TIMING redact=%.2fs", t_redact)
            # Post-redaction: verify no original PII survived in the output.
            from server import verify
            t_verify_start = time.time()
            verified = await inference.run_blocking(
                verify.can_verify, out_path, ext
            )
            if verified:
                leaked = await inference.run_blocking(
                    verify.find_leaks, out_path, ext, spans
                )
            else:
                logger.warning(
                    "Leak verification is blind on %s: the redacted output has "
                    "no text layer (scanned source), so surviving PII inside "
                    "the page image cannot be detected.",
                    Path(file.filename).name,
                )
            t_verify = time.time() - t_verify_start
            logger.info("TIMING verify=%.2fs verified=%s", t_verify, verified)
            if leaked:
                logger.warning(
                    "Redaction verification: %d PII string(s) leaked in %s: %r",
                    len(leaked), Path(file.filename).name, leaked[:5],
                )
            # Keep the original name with an _ANONIMIZED tag so the user can
            # tell the redacted copy apart without renaming it.
            download_name = f"{Path(file.filename).stem}_ANONIMIZED{ext}"
            download_token = _register_download(out_path, download_name)

            # Optional: hand the redacted output to markitdown so the user can
            # paste it into an LLM. The redacted text (with placeholders
            # already applied) doubles as the fallback for scanned PDFs whose
            # redacted copy has no text layer for markitdown to read.
            if _bool_form(also_markdown):
                markdown_token, markdown_name = _register_markdown_export(
                    out_path,
                    ext,
                    original_filename=Path(file.filename).name,
                    fallback_text=result.redacted_text,
                )

        payload = result.to_dict()
        warning = payload.get("warning")
        if leaked:
            leak_msg = (
                f"Se detectaron {len(leaked)} fragmento(s) de PII que no pudieron "
                "eliminarse del documento anonimizado. Revise el resultado antes de compartirlo."
            )
            warning = f"{warning}\n{leak_msg}" if warning else leak_msg
        elif download_token and not verified:
            # Never let an empty leak list read as "verified clean" when the
            # check could not see the page content at all.
            blind_msg = (
                "No se pudo verificar el documento anonimizado: procede de un "
                "escaneado y la copia resultante no tiene capa de texto, así que "
                "no es posible comprobar automáticamente si algún dato sobrevive "
                "dentro de la imagen. Revíselo manualmente antes de compartirlo."
            )
            warning = f"{warning}\n{blind_msg}" if warning else blind_msg
        total = time.time() - t_total_start
        logger.info(
            "TIMING total=%.2fs (extract=%.2f detect=%.2f redact=%.2f verify=%.2f)",
            total, t_extract, t_detect, t_redact, t_verify,
        )
        return {
            "detected_spans": payload["detected_spans"],
            "summary": payload["summary"],
            "warning": warning,
            "leaked_pii_count": len(leaked),
            # True end-to-end wall time. Previously this only covered detection,
            # which on a scanned file hid more than half the wait.
            "elapsed": round(total, 3),
            "timings": {
                "extract": round(t_extract, 3),
                "detect": round(t_detect, 3),
                "redact": round(t_redact, 3),
                "verify": round(t_verify, 3),
                "total": round(total, 3),
            },
            "verified": verified,
            "download_token": download_token,
            "download_name": download_name,
            "markdown_token": markdown_token,
            "markdown_name": markdown_name,
            "mode": mode,
        }
    finally:
        # Drop the per-request extraction cache so PII text doesn't linger
        # in the process address space longer than needed.
        from server import pdf_ops
        pdf_ops.clear_extract_cache()
        _safe_unlink(in_path)


@app.post("/api/redact-file/apply")
async def api_redact_file_apply(
    request: Request,
    file: UploadFile = File(...),
    spans_json: str = Form(...),
    save_example: str = Form("false"),
    also_markdown: str = Form("false"),
) -> dict:
    """Re-apply a user-curated span list to a re-uploaded document.

    The frontend uses this after a human-in-the-loop review pass on
    ``/api/redact-file`` results: the user un-checks, deletes, edits or adds
    spans in the UI, then hits this endpoint with the same file and the final
    span list. The pipeline is NOT run again — we produce the redacted
    document directly from the caller-supplied spans, so their review is the
    single source of truth.

    ``spans_json`` is a JSON array whose entries mirror ``DetectedSpan``:
    ``{"start": int, "end": int, "text": str, "placeholder": str,
    "label": str}``. Only ``text`` and ``placeholder`` are used by the DOCX
    path; the PDF path also needs ``start``/``end`` for offset-based redaction.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in DOC_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Apply endpoint only supports PDF/DOCX (got {ext}).",
        )
    try:
        raw_spans = json.loads(spans_json)
        if not isinstance(raw_spans, list):
            raise ValueError("spans_json must be a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid spans_json: {exc}")

    spans = [_SpanFromClient(**s) for s in raw_spans]

    t_total_start = time.time()
    fd, in_path = tempfile.mkstemp(suffix=ext, prefix="apply_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(await file.read())
        if await request.is_disconnected():
            return {"cancelled": True}
        if not spans:
            # Nothing to redact — return the input as-is under a new token so
            # the caller still gets a downloadable copy.
            out_path = _copy_to_temp(in_path, ext)
        elif ext == ".pdf":
            out_path = await inference.run_blocking(
                redaction.redact_pdf, in_path, spans
            )
        else:
            out_path = await inference.run_blocking(
                redaction.redact_docx, in_path, spans
            )
        from server import verify
        leaked = await inference.run_blocking(
            verify.find_leaks, out_path, ext, spans
        )
        download_name = f"{Path(file.filename).stem}_ANONIMIZED{ext}"
        download_token = _register_download(out_path, download_name)

        # Optional Markdown output — same feature as /api/redact-file. The
        # fallback text for scanned PDFs is derived by re-applying the caller's
        # spans to the freshly re-extracted plain text.
        markdown_token = None
        markdown_name = None
        if _bool_form(also_markdown):
            fallback = await _fallback_redacted_text(in_path, ext, spans)
            markdown_token, markdown_name = _register_markdown_export(
                out_path,
                ext,
                original_filename=Path(file.filename).name,
                fallback_text=fallback,
            )

        warning = None
        if leaked:
            warning = (
                f"Se detectaron {len(leaked)} fragmento(s) de PII que no pudieron "
                "eliminarse. Revise el resultado antes de compartirlo."
            )

        # Optionally keep the reviewer's curated spans as a gold-set example
        # (Phase 7). We re-extract the plain text so the stored offsets match
        # what detection saw; extraction is deterministic, so the client's
        # offsets line up with this text.
        captured = None
        if str(save_example).lower() in ("1", "true", "yes") and spans:
            try:
                if ext == ".pdf":
                    text = await inference.run_blocking(
                        redaction.extract_text_from_pdf, in_path)
                else:
                    text = await inference.run_blocking(
                        redaction.extract_text_from_docx, in_path)
                if text:
                    from server import dataset
                    gold_spans = [
                        {"start": s.start, "end": s.end, "label": s.label}
                        for s in spans
                    ]
                    captured = dataset.get_store().append_example(text, gold_spans)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not capture gold example: %s", exc)

        return {
            "download_token": download_token,
            "download_name": download_name,
            "markdown_token": markdown_token,
            "markdown_name": markdown_name,
            "applied_span_count": len(spans),
            "leaked_pii_count": len(leaked),
            "warning": warning,
            "elapsed": round(time.time() - t_total_start, 3),
            "captured": captured,
        }
    finally:
        from server import pdf_ops
        pdf_ops.clear_extract_cache()
        _safe_unlink(in_path)


# --------------------------------------------------------------------------
#  API: Markdown conversion without anonymization (standalone MarkItDown)
# --------------------------------------------------------------------------
# Formats markitdown can convert on its own without going through the redaction
# pipeline. Wider than DOC_EXTS because there is no OCR fallback to worry about
# here — if markitdown can read it, we serve it.
MARKDOWN_ONLY_EXTS = {
    ".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls",
    ".html", ".htm", ".csv", ".json", ".xml", ".txt", ".md",
}


@app.post("/api/markdown")
async def api_convert_to_markdown(
    request: Request,
    file: UploadFile = File(...),
) -> dict:
    """Convert an uploaded document to Markdown *without* anonymizing it.

    Standalone use of the MarkItDown add-on: the caller uploads a document and
    receives a one-time download token for the resulting ``.md``. No PII
    detection or redaction runs; the output may contain sensitive data. The
    frontend surfaces this clearly with a banner and a confirmation dialog.

    Same offline guarantee as the Markdown export in the anonymization flow:
    the engine is built with ``llm_client=None`` and ``enable_plugins=False``,
    and :mod:`tests.test_offline_guarantee` blocks the network-facing
    ``markitdown`` extras from ever being installed.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in MARKDOWN_ONLY_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato no soportado por MarkItDown: {ext}",
        )

    fd, in_path = tempfile.mkstemp(suffix=ext, prefix="md_only_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(await file.read())
        if await request.is_disconnected():
            return {"cancelled": True}

        # markitdown is CPU-bound and can block for seconds on big files; offload
        # to the inference thread pool so /api/health stays responsive.
        from server import markdown_export
        t0 = time.time()
        markdown = await inference.run_blocking(
            markdown_export.to_markdown, in_path, ext,
        )
        elapsed = time.time() - t0
        if not markdown or not markdown.strip():
            raise HTTPException(
                status_code=422,
                detail="MarkItDown no pudo extraer texto de este archivo.",
            )

        stem = Path(file.filename or "documento").stem or "documento"
        md_name = f"{stem}.md"
        fd_out, md_path = tempfile.mkstemp(suffix=".md", prefix="markdown_only_")
        with os.fdopen(fd_out, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        token = _register_download(md_path, md_name)

        return {
            "download_token": token,
            "download_name": md_name,
            "elapsed": round(elapsed, 3),
            "char_count": len(markdown),
        }
    finally:
        _safe_unlink(in_path)


@app.get("/api/download/{token}")
def api_download(token: str) -> FileResponse:
    """Stream a redacted output file once, then delete it from disk."""
    entry = _pop_download(token)
    if not entry or not os.path.exists(entry[0]):
        raise HTTPException(status_code=404, detail="File not found or expired.")
    path, download_name = entry
    return FileResponse(
        path,
        filename=download_name,
        background=BackgroundTask(_safe_unlink, path),
    )


# --------------------------------------------------------------------------
#  API: custom dictionary (user-editable terms to always anonymize)
# --------------------------------------------------------------------------
@app.get("/api/dictionary")
def api_dictionary_list() -> dict:
    """Return the current dictionary plus the canonical label suggestions."""
    from server import custom_dict

    return {
        "terms": custom_dict.get_store().export_terms(),
        "labels": list(custom_dict.CANONICAL_LABELS),
        "match_modes": list(custom_dict.MATCH_MODES),
    }


@app.post("/api/dictionary")
def api_dictionary_add(entry: DictEntryIn) -> dict:
    """Add one term (or update label/enabled if the same target exists)."""
    from server import custom_dict

    err = custom_dict.validate_entry(entry.term, entry.match)
    if err:
        raise HTTPException(status_code=400, detail=err)
    saved = custom_dict.get_store().add(
        term=entry.term, label=entry.label, match=entry.match,
        enabled=entry.enabled,
    )
    return saved.to_dict()


@app.put("/api/dictionary/{entry_id}")
def api_dictionary_update(entry_id: str, patch: DictEntryPatch) -> dict:
    """Update fields of an existing term."""
    from server import custom_dict

    fields = {k: v for k, v in patch.model_dump().items() if v is not None}
    if "term" in fields or "match" in fields:
        term = fields.get("term", "")
        match = fields.get("match", "smart")
        err = custom_dict.validate_entry(term or "x", match)
        if err:
            raise HTTPException(status_code=400, detail=err)
    updated = custom_dict.get_store().update(entry_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Término no encontrado.")
    return updated.to_dict()


@app.delete("/api/dictionary/{entry_id}")
def api_dictionary_delete(entry_id: str) -> dict:
    """Remove a term from the dictionary."""
    from server import custom_dict

    if not custom_dict.get_store().remove(entry_id):
        raise HTTPException(status_code=404, detail="Término no encontrado.")
    return {"removed": entry_id}


@app.post("/api/dictionary/import")
def api_dictionary_import(payload: DictImportIn) -> dict:
    """Smart-merge an imported dictionary (union, keep local on conflict)."""
    from server import custom_dict

    return custom_dict.get_store().import_terms(payload.terms)


@app.get("/api/dictionary/export")
def api_dictionary_export() -> Response:
    """Download the whole dictionary as a shareable JSON file."""
    from server import custom_dict

    body = json.dumps(
        {"version": 1, "terms": custom_dict.get_store().export_terms()},
        ensure_ascii=False, indent=2,
    )
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=diccionario-anonimizador.json"
        },
    )


# --------------------------------------------------------------------------
#  API: gold set + evaluation (Phase 7 — measure detection quality)
# --------------------------------------------------------------------------
@app.post("/api/dataset/capture")
def api_dataset_capture(payload: CaptureIn) -> dict:
    """Append one reviewed example to the local gold set.

    Called from the human-in-the-loop review flow when the user opts to keep a
    curated result as an evaluation example. The spans are the reviewer's final,
    corrected list — the ground truth we score against later. Stored locally and
    git-ignored (it contains real PII).
    """
    from server import dataset

    return dataset.get_store().append_example(payload.text, payload.spans)


@app.get("/api/dataset/stats")
def api_dataset_stats() -> dict:
    """How many examples/spans the gold set holds, broken down by label."""
    from server import dataset

    return dataset.get_store().stats()


@app.post("/api/dataset/evaluate")
async def api_dataset_evaluate(payload: EvaluateIn) -> dict:
    """Score the whole gold set through the real pipeline in ``mode``.

    Runs detection on every stored example and reports precision/recall/F1
    (overall and per label) plus any leaks — gold PII strings that survived
    redaction. This is the objective "is it getting more precise?" answer.
    """
    from server import dataset, evaluation

    examples = dataset.get_store().load_examples()
    if not examples:
        return {"examples": 0, "detail": "El gold set está vacío."}
    # Detection must go through the model, which is async and serialized on the
    # inference pool; gather results first, then score synchronously.
    results = []
    for ex in examples:
        results.append(await inference.redact(ex.text, mode=payload.mode))
    report = evaluation.score_examples(examples, results, mode=payload.mode)
    return report.as_dict()


# --------------------------------------------------------------------------
#  API: updates (sync endpoints run in Starlette's threadpool)
# --------------------------------------------------------------------------
@app.get("/api/updates")
def api_updates() -> dict:
    """Check for app and model updates."""
    from server import updates

    return updates.get_updates()


@app.post("/api/updates/app")
def api_update_app() -> dict:
    """Install the latest app version and schedule a restart."""
    from server import updates

    return updates.install_app_update()


@app.post("/api/updates/model")
def api_update_model() -> dict:
    """Download the latest model checkpoint and reload it."""
    from server import updates

    return updates.install_model_update()


# --------------------------------------------------------------------------
#  API: diagnostics & shutdown
# --------------------------------------------------------------------------
@app.get("/api/diagnostics")
def api_diagnostics() -> Response:
    """Return a small .zip with version, environment and recent logs.

    Lets the user download a support bundle to send. No secrets are included
    (only which env vars are *set*, never their values).
    """
    import io
    import json
    import platform
    import sys
    import zipfile

    from app_update import get_local_version

    cp = inference.checkpoint_dir()
    info = {
        "version": get_local_version(),
        "platform": platform.platform(),
        "python": sys.version,
        "model": inference.status(),
        "checkpoint_dir": str(cp),
        "checkpoint_present": inference._checkpoint_is_valid(cp),
        "env_set": {
            k: bool(os.environ.get(k))
            for k in (
                "OPF_CHECKPOINT", "HF_HOME", "TIKTOKEN_CACHE_DIR",
                "PF_HOST", "PF_PORT", "PF_LOG_DIR", "TEMP",
            )
        },
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("info.json", json.dumps(info, indent=2, default=str))
        if _LOG_PATH and _LOG_PATH.exists():
            try:
                text = _LOG_PATH.read_text(encoding="utf-8", errors="replace")
                zf.writestr("privacy-filter.log", text[-200_000:])  # last ~200 KB
            except OSError:
                pass
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=privacy-filter-diagnostics.zip"
        },
    )


@app.post("/api/shutdown")
def api_shutdown() -> dict:
    """Stop the server (the 'Quit' button; the process has no console)."""
    logger.info("Shutdown requested via API")
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return {"status": "stopping"}


# --------------------------------------------------------------------------
#  Static frontend (mounted last so /api/* takes precedence)
# --------------------------------------------------------------------------
def _mount_frontend() -> None:
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True),
                  name="frontend")
        logger.info("Serving frontend from %s", FRONTEND_DIST)
    else:
        logger.warning(
            "Frontend build not found at %s. Run `npm run build` in frontend/. "
            "The API is still available under /api.", FRONTEND_DIST,
        )

        @app.get("/")
        def _no_frontend() -> dict:
            return {
                "message": "Frontend not built. Run `npm run build` in frontend/.",
                "api": "/api",
            }


_mount_frontend()


def _ensure_std_streams() -> None:
    """Under pythonw.exe (no console) sys.stdout/stderr are None, which makes
    uvicorn's stream logging crash on startup. Point them at a file so the
    server runs windowless and any output is captured."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log_dir = Path(os.environ.get("PF_LOG_DIR") or (PROJECT_DIR / "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "server-stdout.log", "a", buffering=1, encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not set up std streams: %s", exc)


def main() -> None:
    """Launch uvicorn, trying successive ports if the default is busy.

    Host and starting port can be overridden with PF_HOST / PF_PORT (the
    portable build sets PF_HOST=127.0.0.1 to stay local-only).
    """
    import uvicorn

    _ensure_std_streams()

    host = os.environ.get("PF_HOST", "0.0.0.0")
    port = int(os.environ.get("PF_PORT", "7860"))
    max_port = port + 10
    while port <= max_port:
        try:
            logger.info("Open http://localhost:%d", port)
            # log_config=None: don't let uvicorn install its own stdout/stderr
            # stream handlers (they break under pythonw); our root file handler
            # already captures uvicorn's logs via propagation.
            uvicorn.run(app, host=host, port=port, workers=1, log_config=None)
            break
        except OSError as e:
            if "address already in use" in str(e).lower() or "10048" in str(e):
                logger.warning("Port %d in use, trying %d...", port, port + 1)
                port += 1
            else:
                logger.error("Server failed to start: %s", e, exc_info=True)
                raise
        except Exception as e:  # noqa: BLE001
            logger.error("Server crashed on startup: %s", e, exc_info=True)
            raise


if __name__ == "__main__":
    main()
