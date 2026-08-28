"""Core redaction API: text, file upload, human-reviewed re-apply, download.

The heaviest routes in the app — extraction, detection, redaction, leak
verification and the optional Markdown export all happen here.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from server import downloads, inference, redaction
from server import messages

logger = logging.getLogger("privacy_filter")

router = APIRouter()

# Identifies this server process. A client that sees a different value knows the
# process it was talking to is gone and a new one is answering — the signal the
# update flow waits on to reload the page after a restart.
INSTANCE_ID = uuid.uuid4().hex

# Accepted upload extensions (mirrors the old Gradio file_types list).
TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".xml", ".html"}
DOC_EXTS = {".pdf", ".docx"}


class RedactRequest(BaseModel):
    text: str
    # Precision/recall operating point. Unknown values fall back to "balanced"
    # in :func:`server.inference.redact` — no explicit validator here so the
    # API stays forward-compatible with future presets.
    mode: str = "balanced"


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
        downloads.safe_unlink(md_path)
        return None, None

    return downloads.register(md_path, md_name), md_name


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


@router.get("/api/version")
def api_version() -> dict:
    """Return the local application version."""
    from app_update import get_local_version

    return {"version": get_local_version()}


@router.get("/api/health")
def api_health() -> dict:
    """Return model load state (for showing a loading indicator in the UI).

    Carries ``instance`` so the client can tell a restart apart from a server
    that never went away.
    """
    return {**inference.status(), "instance": INSTANCE_ID}


@router.post("/api/redact")
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
    # ``to_dict`` carries opf's own English prose in "warning". Replace it with
    # the coded form so the backend never ships user-facing text; the Spanish
    # sentence is built in frontend/src/messages.ts.
    if payload.pop("warning", None):
        payload["warnings"] = [messages.message(messages.TOKENIZER_DECODE_MISMATCH)]
    else:
        payload["warnings"] = []
    payload["elapsed"] = round(elapsed, 3)
    payload["mode"] = req.mode
    return payload


@router.post("/api/redact-file")
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
        raise HTTPException(
            status_code=400,
            detail=messages.message(messages.UNSUPPORTED_FILE_TYPE, ext=ext),
        )

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
                raise HTTPException(
                    status_code=422,
                    detail=messages.message(messages.PDF_UNREADABLE),
                )
        elif ext == ".docx":
            text = await inference.run_blocking(
                redaction.extract_text_from_docx, in_path)
            if text is None:
                raise HTTPException(
                    status_code=422,
                    detail=messages.message(messages.DOCX_UNREADABLE),
                )
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
            download_token = downloads.register(out_path, download_name)

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
        warnings: list[dict] = []
        if payload.get("warning"):
            warnings.append(messages.message(messages.TOKENIZER_DECODE_MISMATCH))
        if leaked:
            warnings.append(
                messages.message(messages.LEAK_DETECTED, count=len(leaked))
            )
        elif download_token and not verified:
            # Never let an empty leak list read as "verified clean" when the
            # check could not see the page content at all.
            warnings.append(
                messages.message(messages.VERIFICATION_UNAVAILABLE_SCANNED)
            )
        total = time.time() - t_total_start
        logger.info(
            "TIMING total=%.2fs (extract=%.2f detect=%.2f redact=%.2f verify=%.2f)",
            total, t_extract, t_detect, t_redact, t_verify,
        )
        return {
            "detected_spans": payload["detected_spans"],
            "summary": payload["summary"],
            "warnings": warnings,
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
        downloads.safe_unlink(in_path)


@router.post("/api/redact-file/apply")
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
            detail=messages.message(messages.APPLY_REQUIRES_PDF_OR_DOCX, ext=ext),
        )
    try:
        raw_spans = json.loads(spans_json)
        if not isinstance(raw_spans, list):
            raise ValueError("spans_json must be a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=messages.message(messages.INVALID_SPANS_JSON, error=str(exc)),
        )

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
        download_token = downloads.register(out_path, download_name)

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

        warnings: list[dict] = []
        if leaked:
            warnings.append(
                messages.message(messages.LEAK_DETECTED, count=len(leaked))
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
            "warnings": warnings,
            "elapsed": round(time.time() - t_total_start, 3),
            "captured": captured,
        }
    finally:
        from server import pdf_ops
        pdf_ops.clear_extract_cache()
        downloads.safe_unlink(in_path)


@router.get("/api/download/{token}")
def api_download(token: str) -> FileResponse:
    """Stream a redacted output file once, then delete it from disk."""
    entry = downloads.pop(token)
    if not entry or not os.path.exists(entry[0]):
        raise HTTPException(
            status_code=404,
            detail=messages.message(messages.FILE_NOT_FOUND_OR_EXPIRED),
        )
    path, download_name = entry
    return FileResponse(
        path,
        filename=download_name,
        background=BackgroundTask(downloads.safe_unlink, path),
    )
