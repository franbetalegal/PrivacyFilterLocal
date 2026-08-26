"""Standalone Markdown conversion (no anonymization)."""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from server import downloads, inference
from server import messages

logger = logging.getLogger("privacy_filter")

router = APIRouter()

# Formats markitdown can convert on its own without going through the redaction
# pipeline. Wider than DOC_EXTS because there is no OCR fallback to worry about
# here — if markitdown can read it, we serve it.
MARKDOWN_ONLY_EXTS = {
    ".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls",
    ".html", ".htm", ".csv", ".json", ".xml", ".txt", ".md",
}


@router.post("/api/markdown")
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
            detail=messages.message(messages.MARKDOWN_UNSUPPORTED_FORMAT, ext=ext),
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
                detail=messages.message(messages.MARKDOWN_NO_TEXT),
            )

        stem = Path(file.filename or "documento").stem or "documento"
        md_name = f"{stem}.md"
        fd_out, md_path = tempfile.mkstemp(suffix=".md", prefix="markdown_only_")
        with os.fdopen(fd_out, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        token = downloads.register(md_path, md_name)

        return {
            "download_token": token,
            "download_name": md_name,
            "elapsed": round(elapsed, 3),
            "char_count": len(markdown),
        }
    finally:
        downloads.safe_unlink(in_path)
