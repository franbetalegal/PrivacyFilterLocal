"""Auto-update module for Privacy Filter PII model.

Checks HuggingFace repository for model updates and downloads them.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError


HF_MODEL_REPO = "openai/privacy-filter"
HF_API_URL = f"https://huggingface.co/api/models/{HF_MODEL_REPO}"
MODEL_DIR = Path.home() / ".opf" / "privacy_filter"
LOCAL_DATE_FILE = MODEL_DIR / ".last_updated"


def get_local_model_date() -> Optional[str]:
    """Read the local model last updated date."""
    try:
        if LOCAL_DATE_FILE.is_file():
            return LOCAL_DATE_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def save_local_model_date(date_str: str) -> None:
    """Save the model last updated date locally."""
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_DATE_FILE.write_text(date_str, encoding="utf-8")
    except Exception:
        pass


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
    except Exception:
        return None


@dataclass
class ModelUpdateInfo:
    """Information about an available model update."""
    update_available: bool
    current_date: Optional[str]
    latest_date: Optional[str]
    error: Optional[str]


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
            return ModelUpdateInfo(
                update_available=False,
                current_date=remote_date,
                latest_date=remote_date,
                error=None,
            )
        return ModelUpdateInfo(
            update_available=False,
            current_date=None,
            latest_date=None,
            error=None,
        )

    try:
        remote_date = get_remote_model_date()

        if remote_date is None:
            return ModelUpdateInfo(
                update_available=False,
                current_date=current_date,
                latest_date=None,
                error="Could not fetch remote model date",
            )

        if current_date is None:
            return ModelUpdateInfo(
                update_available=False,
                current_date=None,
                latest_date=remote_date,
                error=None,
            )

        update_available = remote_date > current_date

        return ModelUpdateInfo(
            update_available=update_available,
            current_date=current_date,
            latest_date=remote_date,
            error=None,
        )

    except URLError as exc:
        return ModelUpdateInfo(
            update_available=False,
            current_date=current_date,
            latest_date=None,
            error=f"Network error: {exc}",
        )
    except Exception as exc:
        return ModelUpdateInfo(
            update_available=False,
            current_date=current_date,
            latest_date=None,
            error=str(exc),
        )


def download_model_update(
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> tuple[bool, str]:
    """Download and install the model update.

    Removes the local model cache and re-downloads from HuggingFace.
    Uses the existing checkpoint_download module for the actual download.

    Args:
        progress_callback: Optional callback(status_message, progress_0_to_1)

    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        if progress_callback:
            progress_callback("Removing current model...", 0.1)

        # Remove existing model
        if MODEL_DIR.exists():
            shutil.rmtree(str(MODEL_DIR))

        if progress_callback:
            progress_callback("Downloading new model...", 0.3)

        # Import and use the existing checkpoint download
        import sys
        sys.path.insert(0, str(Path(__file__).parent / "privacy-filter"))
        from opf._common.checkpoint_download import ensure_default_checkpoint

        if progress_callback:
            progress_callback("Downloading model weights...", 0.5)

        ensure_default_checkpoint()

        if progress_callback:
            progress_callback("Saving update timestamp...", 0.9)

        # Save the remote date as local date
        remote_date = get_remote_model_date()
        if remote_date:
            save_local_model_date(remote_date)

        if progress_callback:
            progress_callback("Done!", 1.0)

        return True, "Model updated successfully"

    except Exception as exc:
        return False, f"Update failed: {exc}"
