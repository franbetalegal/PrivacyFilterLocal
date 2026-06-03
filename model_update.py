"""Auto-update module for Privacy Filter PII model.

Checks HuggingFace repository for model updates and downloads them.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger("privacy_filter.model_update")

# Ensure privacy-filter package is importable
_PRIVACY_FILTER_DIR = str(Path(__file__).parent / "privacy-filter")
if _PRIVACY_FILTER_DIR not in sys.path:
    sys.path.insert(0, _PRIVACY_FILTER_DIR)


HF_MODEL_REPO = "openai/privacy-filter"
HF_API_URL = f"https://huggingface.co/api/models/{HF_MODEL_REPO}"
MODEL_DIR = Path.home() / ".opf" / "privacy_filter"
LOCAL_DATE_FILE = MODEL_DIR / ".last_updated"


def _is_checkpoint_valid(model_dir: Path) -> bool:
    """Return True if ``model_dir`` looks like a complete OPF checkpoint."""
    if not model_dir.is_dir():
        return False
    if not (model_dir / "config.json").is_file():
        return False
    return any(model_dir.glob("*.safetensors"))


def _is_partial_checkpoint(model_dir: Path) -> bool:
    """Return True if ``model_dir`` exists but is missing required files.

    A partial checkpoint can be a previous failed download or a corrupt
    install; in either case the safest thing to do is wipe it and start over.
    """
    if not model_dir.exists() or not model_dir.is_dir():
        return False
    return not _is_checkpoint_valid(model_dir)


def get_local_model_date() -> Optional[str]:
    """Read the local model last updated date."""
    try:
        if LOCAL_DATE_FILE.is_file():
            return LOCAL_DATE_FILE.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.debug("Could not read local model date: %s", exc)
    return None


def save_local_model_date(date_str: str) -> None:
    """Save the model last updated date locally."""
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_DATE_FILE.write_text(date_str, encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not save local model date: %s", exc)


def get_remote_model_date() -> Optional[str]:
    """Fetch the last modified date from HuggingFace API."""
    try:
        req = Request(HF_API_URL, headers={
            "Accept": "application/json",
            "User-Agent": "PrivacyFilter-App",
        })
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        last_modified = data.get("lastModified") or data.get("last_modified")
        if last_modified:
            return last_modified[:10] if len(last_modified) > 10 else last_modified

        # Fallback: check siblings for any date info
        return None
    except Exception as exc:
        logger.debug("Could not fetch remote model date: %s", exc)
        return None


@dataclass
class ModelUpdateInfo:
    """Information about an available model update."""
    update_available: bool
    current_date: Optional[str]
    latest_date: Optional[str]
    error: Optional[str] = None


def check_for_model_update() -> ModelUpdateInfo:
    """Check HuggingFace for model updates.

    Compares the local model date with the remote last modified date.
    Returns a ModelUpdateInfo with the comparison result.
    """
    current_date = get_local_model_date()

    # If no local date file exists but model exists, create it from remote
    if current_date is None and MODEL_DIR.exists() and MODEL_DIR.is_dir():
        remote_date = get_remote_model_date()
        if remote_date:
            save_local_model_date(remote_date)
            return ModelUpdateInfo(False, remote_date, remote_date)
        return ModelUpdateInfo(False, None, None)

    try:
        remote_date = get_remote_model_date()

        if remote_date is None:
            return ModelUpdateInfo(
                False, current_date, None,
                error="Could not fetch remote model date",
            )

        if current_date is None:
            return ModelUpdateInfo(False, None, remote_date)

        return ModelUpdateInfo(remote_date > current_date, current_date, remote_date)

    except URLError as exc:
        return ModelUpdateInfo(False, current_date, None, error=f"Network error: {exc}")
    except Exception as exc:
        return ModelUpdateInfo(False, current_date, None, error=str(exc))


def _download_checkpoint_to(target_dir: Path) -> None:
    """Download the OPF checkpoint into ``target_dir`` using huggingface_hub.

    The download writes files into ``target_dir/original/`` and then promotes
    them to ``target_dir/`` to match the layout ``OPF`` expects.

    Raises:
        ImportError: If ``huggingface_hub`` is not installed.
        RuntimeError: If the download fails or the result is missing files.
    """
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=HF_MODEL_REPO,
        local_dir=str(target_dir),
        allow_patterns=["original/*"],
    )
    original = target_dir / "original"
    if not original.is_dir():
        raise RuntimeError(
            f"Downloaded checkpoint is missing expected subtree: {original}"
        )
    for path in original.iterdir():
        destination = target_dir / path.name
        if destination.exists():
            raise RuntimeError(
                "Cannot promote downloaded checkpoint file because "
                f"destination already exists: {destination}"
            )
        shutil.move(str(path), str(destination))
    original.rmdir()


def download_model_update(
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> tuple[bool, str]:
    """Download and install the model update atomically.

    The new checkpoint is first written to a temporary directory next to the
    model folder. Only after the download finishes and validates is the
    existing model replaced. If anything fails (network error, partial
    download, validation error) the previous working model is left
    untouched.

    If the existing ``MODEL_DIR`` is detected in a partial state (e.g. from
    a previously interrupted download) it is wiped before downloading so the
    new install starts clean.

    Args:
        progress_callback: Optional callback(status_message, progress_0_to_1)

    Returns:
        Tuple of (success: bool, message: str)
    """
    new_dir: Optional[Path] = None
    try:
        if _is_partial_checkpoint(MODEL_DIR):
            if progress_callback:
                progress_callback("Removing partial model...", 0.1)
            shutil.rmtree(MODEL_DIR)

        parent = MODEL_DIR.parent
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="opf_download_", dir=str(parent)
        ) as tmp_name:
            new_dir = Path(tmp_name)
            if progress_callback:
                progress_callback("Downloading new model...", 0.3)
            if progress_callback:
                progress_callback("Downloading model weights...", 0.5)
            _download_checkpoint_to(new_dir)

            if not _is_checkpoint_valid(new_dir):
                raise RuntimeError(
                    "Downloaded checkpoint is missing config.json or "
                    "safetensors files"
                )

            if progress_callback:
                progress_callback("Activating new model...", 0.85)
            if MODEL_DIR.exists():
                shutil.rmtree(MODEL_DIR)
            shutil.move(str(new_dir), str(MODEL_DIR))
            new_dir = None  # moved into place; do not clean up

        if progress_callback:
            progress_callback("Saving update timestamp...", 0.9)

        remote_date = get_remote_model_date()
        if remote_date:
            save_local_model_date(remote_date)

        if progress_callback:
            progress_callback("Done!", 1.0)

        return True, "Model updated successfully"

    except Exception as exc:
        if new_dir is not None:
            shutil.rmtree(new_dir, ignore_errors=True)
        return False, f"Update failed: {exc}"
