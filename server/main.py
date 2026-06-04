"""FastAPI application for Privacy Filter - Local.

Serves the JSON API under ``/api`` and the built React frontend under ``/``.
Run with:  uvicorn server.main:app --host 0.0.0.0 --port 7860
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

# Importing the package configures sys.path for ``opf`` / update modules.
from server import PROJECT_DIR
from server import inference, redaction

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("privacy_filter")

FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"

app = FastAPI(title="Privacy Filter - Local", version="2.0.0")

# Permissive CORS so the Vite dev server (different port) can call the API.
# In production the frontend is served from the same origin, so this is a no-op.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
#  Schemas
# --------------------------------------------------------------------------
class RedactRequest(BaseModel):
    text: str


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
                "empty": True}
    start = time.time()
    result = await inference.redact(text)
    elapsed = time.time() - start
    payload = result.to_dict()
    payload["elapsed"] = round(elapsed, 3)
    return payload


@app.post("/api/redact-file")
async def api_redact_file(file: UploadFile = File(...)) -> dict:
    """Detect PII in an uploaded file.

    For PDF/DOCX a redacted copy is produced and exposed via a one-time
    ``download_token``. The uploaded input is always deleted after processing.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in TEXT_EXTS and ext not in DOC_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    # Persist the upload to a temp file (PyMuPDF/python-docx need a path).
    fd, in_path = tempfile.mkstemp(suffix=ext, prefix="upload_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(await file.read())

        if ext == ".pdf":
            text = redaction.extract_text_from_pdf(in_path)
            if text is None:
                raise HTTPException(status_code=422, detail="Could not read PDF.")
        elif ext == ".docx":
            text = redaction.extract_text_from_docx(in_path)
            if text is None:
                raise HTTPException(status_code=422, detail="Could not read DOCX.")
        else:
            text = Path(in_path).read_text(encoding="utf-8", errors="replace")

        start = time.time()
        result = await inference.redact(text)
        elapsed = time.time() - start
        spans = result.detected_spans

        download_token = None
        download_name = None
        if spans and ext in DOC_EXTS:
            if ext == ".pdf":
                out_path = await inference.run_blocking(
                    redaction.redact_pdf, in_path, spans
                )
            else:
                out_path = await inference.run_blocking(
                    redaction.redact_docx, in_path, spans
                )
            # Keep the original name with an _ANONIMIZED tag so the user can
            # tell the redacted copy apart without renaming it.
            download_name = f"{Path(file.filename).stem}_ANONIMIZED{ext}"
            download_token = _register_download(out_path, download_name)

        payload = result.to_dict()
        return {
            "detected_spans": payload["detected_spans"],
            "summary": payload["summary"],
            "warning": payload.get("warning"),
            "elapsed": round(elapsed, 3),
            "download_token": download_token,
            "download_name": download_name,
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


def main() -> None:
    """Launch uvicorn, trying successive ports if the default is busy.

    Host and starting port can be overridden with PF_HOST / PF_PORT (the
    portable build sets PF_HOST=127.0.0.1 to stay local-only).
    """
    import uvicorn

    host = os.environ.get("PF_HOST", "0.0.0.0")
    port = int(os.environ.get("PF_PORT", "7860"))
    max_port = port + 10
    while port <= max_port:
        try:
            logger.info("Open http://localhost:%d", port)
            uvicorn.run(app, host=host, port=port, workers=1)
            break
        except OSError as e:
            if "address already in use" in str(e).lower() or "10048" in str(e):
                logger.warning("Port %d in use, trying %d...", port, port + 1)
                port += 1
            else:
                raise


if __name__ == "__main__":
    main()
