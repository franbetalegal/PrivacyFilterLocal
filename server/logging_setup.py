"""Rotating file logging for the FastAPI app.

Mirrors stdlib logging to a rotating file so users can send diagnostics; the
resulting path is also read by the ``/api/diagnostics`` endpoint to attach
recent logs to the support bundle it produces.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from server import PROJECT_DIR

logger = logging.getLogger("privacy_filter")

_log_path: Path | None = None


def setup_file_logging() -> Path | None:
    """Mirror logs to a rotating file so users can send diagnostics.

    The directory is PF_LOG_DIR (set by the portable launcher to stay inside the
    app folder) or ``<project>/logs`` by default.
    """
    global _log_path
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
    _log_path = log_path
    return log_path


def get_log_path() -> Path | None:
    return _log_path
