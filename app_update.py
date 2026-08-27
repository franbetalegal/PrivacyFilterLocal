"""Auto-update module for Privacy Filter application.

Downloads updates from GitHub Releases and installs them automatically.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import ssl
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger("privacy_filter.app_update")


GITHUB_REPO = "franbetalegal/PrivacyFilterLocal"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
PROJECT_DIR = Path(__file__).parent
VERSION_FILE = PROJECT_DIR / "VERSION"

# Files/dirs to preserve during update (user config)
PRESERVE_LIST = {
    "start.bat",
    ".env",
    ".git",
}

# Files/dirs to exclude from update
EXCLUDE_LIST = {
    ".git",
    "__pycache__",
    "*.pyc",
    ".update_temp",
}


def ssl_context() -> Optional[ssl.SSLContext]:
    """A TLS context that can verify GitHub, or ``None`` for the default one.

    A python.org framework build on macOS ships no CA bundle until the user
    runs ``Install Certificates.command``, which nobody does. Every
    ``urlopen`` to GitHub then dies with CERTIFICATE_VERIFY_FAILED, and because
    the update check swallows network errors, the app just never reports an
    update — measured on a real 2.6.0 install, where the check had never once
    succeeded. ``certifi`` is already present (a transitive dependency of
    ``huggingface_hub``, and pinned in requirements-server.txt so it stays
    that way), and its bundle is exactly what ``requests`` verifies against, so
    the model download working while the update check failed was purely down to
    which HTTP client each one used.

    Shared with :mod:`model_update`, which talks to HuggingFace over the same
    ``urlopen`` and had the same failure.
    """
    try:
        import certifi
    except ImportError:
        return None
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not build an SSL context from certifi: %s", exc)
        return None


def get_local_version() -> str:
    """Read the local VERSION file."""
    try:
        if VERSION_FILE.is_file():
            return VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.debug("Could not read local version: %s", exc)
    return "0.0.0"


def compare_versions(current: str, latest: str) -> int:
    """Compare semantic versions. Returns:
    -1 if current < latest
     0 if current == latest
     1 if current > latest
    """
    try:
        curr_parts = [int(x) for x in current.split(".")]
        lat_parts = [int(x) for x in latest.split(".")]
        
        for c, l in zip(curr_parts, lat_parts):
            if c < l:
                return -1
            if c > l:
                return 1
        
        if len(curr_parts) < len(lat_parts):
            return -1
        if len(curr_parts) > len(lat_parts):
            return 1
        
        return 0
    except (ValueError, AttributeError):
        return 0


# Release assets are named per platform by .github/workflows/release.yml:
#   PrivacyFilter-linux-vX.Y.Z.tar.gz
#   PrivacyFilter-macos-arm64-vX.Y.Z.tar.gz
#   PrivacyFilter-Setup-vX.Y.Z.exe
# The archive for the platform we are running on is the one to install. Picking
# by suffix alone used to look for ".zip", which no release has ever carried, so
# the archive path was dead and every install fell through to "git pull" —
# which cannot work in an extracted tarball, the very thing the archives are for.
_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".zip")


def _platform_asset_marker() -> str:
    """The substring the asset for this platform must contain, or ``""``.

    Windows ships an NSIS installer rather than an archive; installing it means
    running the installer, a different flow from unpacking files over the
    current ones, so it is deliberately not offered here.
    """
    if sys.platform == "darwin":
        # Only Apple Silicon is built; on an Intel Mac there is no asset to
        # install and saying so beats unpacking arm64 wheels over a working set.
        return "macos-arm64" if platform.machine() == "arm64" else ""
    if sys.platform.startswith("linux"):
        return "linux"
    return ""


def select_asset(assets: list[dict]) -> str:
    """Return the download URL of the release asset for this platform.

    ``""`` when the release carries nothing installable here, which leaves the
    caller on its ``git pull`` path (correct for a clone, and reported as such
    for an extracted archive).
    """
    marker = _platform_asset_marker()
    if not marker:
        return ""
    for asset in assets:
        name = asset.get("name", "")
        if marker not in name.lower():
            continue
        if name.lower().endswith(_ARCHIVE_SUFFIXES):
            return asset.get("browser_download_url", "")
    return ""


@dataclass
class AppUpdateInfo:
    """Information about an available app update."""
    update_available: bool
    current_version: str
    latest_version: str = ""
    changelog: str = ""
    download_url: str = ""
    published_date: str = ""
    error: Optional[str] = None


def check_for_app_update() -> AppUpdateInfo:
    """Check GitHub Releases for app updates.
    
    Returns an AppUpdateInfo with the comparison result. If any step fails
    (network error, missing library, etc.), returns an AppUpdateInfo with
    the error field set and update_available=False.
    """
    current_version = get_local_version()
    
    try:
        req = Request(GITHUB_API_URL, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PrivacyFilter-App",
        })
        with urlopen(req, timeout=10, context=ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
        
        latest_version = data.get("tag_name", "").lstrip("v")
        changelog = data.get("body", "") or ""
        published_date = data.get("published_at", "")[:10] if data.get("published_at") else ""
        
        # The archive built for this platform, if the release carries one.
        download_url = select_asset(data.get("assets", []))
        
        if not latest_version:
            return AppUpdateInfo(
                False, current_version, error="No version found in release"
            )

        update_available = compare_versions(current_version, latest_version) < 0

        return AppUpdateInfo(
            update_available=update_available,
            current_version=current_version,
            latest_version=latest_version,
            changelog=changelog,
            download_url=download_url,
            published_date=published_date,
        )

    except URLError as exc:
        return AppUpdateInfo(False, current_version, error=f"Network error: {exc}")
    except Exception as exc:
        return AppUpdateInfo(False, current_version, error=str(exc))


def _archive_filename(download_url: str) -> str:
    """Local filename for ``download_url``, keeping the suffix intact.

    The suffix is what tells :func:`_extract_archive` which format it has, so
    the download cannot be saved under a fixed name.
    """
    name = download_url.rstrip("/").rsplit("/", 1)[-1]
    if name.lower().endswith(_ARCHIVE_SUFFIXES):
        return name
    return "update.tar.gz"


def _extract_archive(archive_path: Path, extract_dir: Path) -> None:
    """Unpack ``archive_path`` into ``extract_dir``, tar.gz or zip.

    Tar members are extracted with the ``data`` filter, which refuses absolute
    paths, ``..`` traversal, links pointing outside the destination and device
    files. The archive is our own release artifact, but it arrives over the
    network and unpacking it grants write access to the install directory.
    """
    name = archive_path.name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(extract_dir, filter="data")
        return
    with zipfile.ZipFile(archive_path, "r") as zf:
        zf.extractall(extract_dir)


def download_and_install_update(
    download_url: str,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> tuple[bool, str]:
    """Download and install an update from GitHub Releases.
    
    Args:
        download_url: URL of the .tar.gz or .zip archive to download
        progress_callback: Optional callback(status_message, progress_0_to_1)
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    # Module attribute, not a local computation, so a test can point the
    # install at a temporary folder instead of the running one.
    project_dir = PROJECT_DIR
    temp_dir = None
    
    try:
        # Create temp directory
        temp_dir = Path(tempfile.mkdtemp(prefix="opf_update_"))
        archive_path = temp_dir / _archive_filename(download_url)
        extract_dir = temp_dir / "extracted"
        
        if progress_callback:
            progress_callback("Downloading update...", 0.1)
        
        # Download the archive
        req = Request(download_url, headers={
            "User-Agent": "PrivacyFilter-App",
        })
        with urlopen(req, timeout=120, context=ssl_context()) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 8192
            
            with open(archive_path, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and progress_callback:
                        progress = 0.1 + (downloaded / total_size) * 0.4
                        progress_callback("Downloading update...", min(progress, 0.5))
        
        if progress_callback:
            progress_callback("Extracting files...", 0.6)
        
        _extract_archive(archive_path, extract_dir)
        
        # Find the extracted folder (usually starts with repo name)
        extracted_folders = list(extract_dir.iterdir())
        if not extracted_folders:
            return False, "Downloaded archive is empty"
        
        source_dir = extracted_folders[0]
        if not source_dir.is_dir():
            source_dir = extract_dir
        
        if progress_callback:
            progress_callback("Installing update...", 0.7)
        
        # Backup current VERSION
        current_version = get_local_version()
        
        # Update files
        updated_files = 0
        for item in source_dir.rglob("*"):
            if item.is_dir():
                continue
            
            # Get relative path from source
            rel_path = item.relative_to(source_dir)
            
            # Skip preserved files
            if rel_path.name in PRESERVE_LIST or rel_path.parts[0] in PRESERVE_LIST:
                continue
            
            # Skip excluded patterns
            skip = False
            for exclude in EXCLUDE_LIST:
                if exclude.startswith("*"):
                    if rel_path.name.endswith(exclude[1:]):
                        skip = True
                        break
                elif exclude in rel_path.parts:
                    skip = True
                    break
            if skip:
                continue
            
            # Skip archives and update temp
            if rel_path.name.lower().endswith(_ARCHIVE_SUFFIXES) \
                    or ".update_temp" in rel_path.parts:
                continue
            
            # Copy file
            dest_path = project_dir / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_path)
            updated_files += 1
        
        # Update VERSION file
        new_version = ""
        version_file = source_dir / "VERSION"
        if version_file.is_file():
            new_version = version_file.read_text(encoding="utf-8").strip()
        
        if progress_callback:
            progress_callback("Finalizing...", 0.9)
        
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir = None
        
        message = f"Updated from {current_version} to {new_version}" if new_version else f"Updated ({updated_files} files)"
        return True, message
    
    except Exception as exc:
        # Cleanup on error
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        return False, f"Update failed: {exc}"


def restart_app() -> None:
    """Restart the application backend (uvicorn) as a detached process.

    Kept for backwards compatibility; the FastAPI backend normally restarts via
    ``server.updates.restart_server``.
    """
    import subprocess
    import time

    project_dir = Path(__file__).parent
    if os.name == "nt":
        venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = project_dir / ".venv" / "bin" / "python"

    python = str(venv_python) if venv_python.exists() else sys.executable

    creationflags = 0
    if os.name == "nt":
        creationflags = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )

    # Start new process detached
    subprocess.Popen(
        [python, "-m", "server.main"],
        cwd=str(project_dir),
        creationflags=creationflags,
        close_fds=True,
    )

    # Exit current process
    time.sleep(0.5)
    os._exit(0)
