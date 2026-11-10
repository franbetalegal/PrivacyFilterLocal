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


def sweep_expired() -> None:
    """Public: run the TTL sweep. Called on a timer so outputs expire even when
    nobody registers a new download (it used to run only inside ``register``)."""
    _sweep_expired()


# Prefixes of every temp file this app creates: uploads and re-uploads hold the
# original document, the rest hold redacted output or page images.
_TEMP_PREFIXES = ("upload_", "apply_", "md_only_", "markdown_", "markdown_only_",
                  "tess_")
_STALE_TEMP_SECONDS = 600


def sweep_stale_temp_files() -> int:
    """Delete temp files a previous, crashed run of the app left behind.

    A request deletes its files in a ``finally`` block, which a process that
    dies never reaches. What it leaves in the temp folder is the user's
    original, un-anonymised document. Only files with this app's prefixes and
    older than a few minutes are touched, so a second instance that is running
    right now keeps its in-flight files.
    """
    import tempfile

    tmp = tempfile.gettempdir()
    cutoff = time.time() - _STALE_TEMP_SECONDS
    removed = 0
    try:
        names = os.listdir(tmp)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(_TEMP_PREFIXES):
            continue
        path = os.path.join(tmp, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Removed %d leftover temp file(s) from a previous run", removed)
    return removed


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
