"""Make sure opf's tokenizer vocabulary is on disk before anyone needs it.

opf tokenizes with tiktoken's ``o200k_base``. tiktoken fetches that file from
the internet the first time it is asked for it, with ``requests.get`` and no
timeout, and nothing in the app used to ask before the user's first document.
So on a fresh install the first anonymisation also did a download, and behind a
proxy or without a connection it hung for good inside the user's request.

This module does the fetch during the startup preflight instead, with a
timeout, a hash check and a readable failure, and writes the file exactly where
tiktoken's own cache lookup will find it. When the file is already there (a
build that ships it, or any later start) nothing touches the network.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger("privacy_filter.vocab")

URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
TIMEOUT_SECONDS = 60


def cache_dir() -> Path:
    """The directory tiktoken reads its cache from, same precedence as tiktoken."""
    for var in ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR"):
        value = os.environ.get(var)
        if value:
            return Path(value)
    return Path(tempfile.gettempdir()) / "data-gym-cache"


def cache_path() -> Path:
    """File name tiktoken uses for this URL: the SHA-1 of the URL itself."""
    return cache_dir() / hashlib.sha1(URL.encode()).hexdigest()


def is_present() -> bool:
    path = cache_path()
    if not path.is_file():
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == SHA256


def ensure_present() -> None:
    """Download the vocabulary if it is missing or corrupt. Raises on failure."""
    if is_present():
        return
    import requests

    logger.info("Downloading the tokenizer vocabulary from %s", URL)
    response = requests.get(URL, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.content
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("tokenizer vocabulary failed its integrity check")
    target = cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    logger.info("Tokenizer vocabulary stored at %s", target)
