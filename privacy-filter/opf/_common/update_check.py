"""Check for model updates on HuggingFace."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .constants import DEFAULT_MODEL_PATH
from .checkpoint_download import DEFAULT_HF_MODEL_REPO


VERSION_FILE_NAME = ".opf_version"


def _get_version_file() -> Path:
    return DEFAULT_MODEL_PATH.expanduser() / VERSION_FILE_NAME


def save_local_version(commit_hash: str) -> None:
    """Save the local checkpoint commit hash to disk."""
    version_file = _get_version_file()
    try:
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(json.dumps({"commit_hash": commit_hash}), encoding="utf-8")
    except Exception:
        pass


def get_local_version() -> Optional[str]:
    """Read the locally stored commit hash, or None if unavailable."""
    version_file = _get_version_file()
    try:
        if version_file.is_file():
            data = json.loads(version_file.read_text(encoding="utf-8"))
            return data.get("commit_hash")
    except Exception:
        pass
    return None


@dataclass
class UpdateInfo:
    update_available: bool
    local_hash: Optional[str]
    remote_hash: Optional[str]
    remote_date: Optional[str]
    error: Optional[str]


def check_for_update() -> UpdateInfo:
    """Check if a newer model checkpoint is available on HuggingFace.

    Returns an UpdateInfo with the comparison result. If any step fails
    (network error, missing library, etc.), returns an UpdateInfo with the
    error field set and update_available=False.
    """
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return UpdateInfo(
            update_available=False,
            local_hash=None,
            remote_hash=None,
            remote_date=None,
            error="huggingface_hub is not installed",
        )

    local_hash = get_local_version()

    try:
        api = HfApi()
        commits = list(api.list_repo_commits(DEFAULT_HF_MODEL_REPO))
        if not commits:
            return UpdateInfo(
                update_available=False,
                local_hash=local_hash,
                remote_hash=None,
                remote_date=None,
                error="No commits found in remote repo",
            )
        latest = commits[0]
        remote_hash = latest.commit_id
        remote_date = latest.created_at.isoformat() if latest.created_at else None
    except Exception as exc:
        return UpdateInfo(
            update_available=False,
            local_hash=local_hash,
            remote_hash=None,
            remote_date=None,
            error=str(exc),
        )

    if local_hash is None:
        return UpdateInfo(
            update_available=True,
            local_hash=None,
            remote_hash=remote_hash,
            remote_date=remote_date,
            error=None,
        )

    update_available = local_hash != remote_hash
    return UpdateInfo(
        update_available=update_available,
        local_hash=local_hash,
        remote_hash=remote_hash,
        remote_date=remote_date,
        error=None,
    )
