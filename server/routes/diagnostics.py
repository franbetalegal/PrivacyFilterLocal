"""Support bundle download and the 'Quit' button."""

from __future__ import annotations

import logging
import os
import threading

from fastapi import APIRouter, Response

from server import inference, logging_setup

logger = logging.getLogger("privacy_filter")

router = APIRouter()


@router.get("/api/diagnostics")
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
        log_path = logging_setup.get_log_path()
        if log_path and log_path.exists():
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
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


@router.post("/api/shutdown")
def api_shutdown() -> dict:
    """Stop the server (the 'Quit' button; the process has no console)."""
    logger.info("Shutdown requested via API")
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return {"status": "stopping"}
