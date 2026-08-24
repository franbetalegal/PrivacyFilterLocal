"""One-time download token registry.

Shared by every route that hands the caller a file to fetch later
(``/api/redact-file``, ``/api/redact-file/apply``, ``/api/markdown``): a route
registers the path it produced and gets back an opaque token; ``/api/download/
{token}`` resolves it exactly once. A TTL sweep deletes outputs that were
registered but never downloaded, so redacted PII never lingers on disk.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid

logger = logging.getLogger("privacy_filter")

# token -> (path on disk, filename to present, created timestamp). Entries are
# removed when downloaded; the TTL sweep deletes ones never downloaded.
_downloads: dict[str, tuple[str, str, float]] = {}
_downloads_lock = threading.Lock()
_DOWNLOAD_TTL_SECONDS = 1800  # 30 minutes


def safe_unlink(path: str | None) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as exc:
            logger.debug("Could not delete temp file %s: %s", path, exc)


def _sweep_expired() -> None:
    """Delete redacted outputs that were never downloaded within the TTL."""
    now = time.time()
    expired = []
    with _downloads_lock:
        for token, (path, _name, created) in list(_downloads.items()):
            if now - created > _DOWNLOAD_TTL_SECONDS:
                expired.append(path)
                _downloads.pop(token, None)
    for path in expired:
        safe_unlink(path)


def register(path: str, download_name: str) -> str:
    token = uuid.uuid4().hex
    with _downloads_lock:
        _downloads[token] = (path, download_name, time.time())
    _sweep_expired()
    return token


def pop(token: str) -> tuple[str, str] | None:
    with _downloads_lock:
        entry = _downloads.pop(token, None)
    if entry is None:
        return None
    path, name, _created = entry
    return (path, name)
