"""Auto-update module for Privacy Filter application.

Downloads updates from GitHub Releases and installs them automatically.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError


GITHUB_REPO = "franbetalegal/PrivacyFilterLocal"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
VERSION_FILE = Path(__file__).parent / "VERSION"

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


def get_local_version() -> str:
    """Read the local VERSION file."""
    try:
        if VERSION_FILE.is_file():
            return VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
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


@dataclass
class AppUpdateInfo:
    """Information about an available app update."""
    update_available: bool
    current_version: str
    latest_version: str
    changelog: str
    download_url: str
    published_date: str
    error: Optional[str]


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
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        
        latest_version = data.get("tag_name", "").lstrip("v")
        changelog = data.get("body", "") or ""
        published_date = data.get("published_at", "")[:10] if data.get("published_at") else ""
        
        # Find ZIP asset (optional - if not found, use git pull)
        download_url = ""
        for asset in data.get("assets", []):
            if asset.get("name", "").endswith(".zip"):
                download_url = asset.get("browser_download_url", "")
                break
        
        if not latest_version:
            return AppUpdateInfo(
                update_available=False,
                current_version=current_version,
                latest_version="",
                changelog="",
                download_url="",
                published_date="",
                error="No version found in release",
            )
        
        update_available = compare_versions(current_version, latest_version) < 0
        
        return AppUpdateInfo(
            update_available=update_available,
            current_version=current_version,
            latest_version=latest_version,
            changelog=changelog,
            download_url=download_url,
            published_date=published_date,
            error=None,
        )
    
    except URLError as exc:
        return AppUpdateInfo(
            update_available=False,
            current_version=current_version,
            latest_version="",
            changelog="",
            download_url="",
            published_date="",
            error=f"Network error: {exc}",
        )
    except Exception as exc:
        return AppUpdateInfo(
            update_available=False,
            current_version=current_version,
            latest_version="",
            changelog="",
            download_url="",
            published_date="",
            error=str(exc),
        )


def download_and_install_update(
    download_url: str,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> tuple[bool, str]:
    """Download and install an update from GitHub Releases.
    
    Args:
        download_url: URL of the ZIP file to download
        progress_callback: Optional callback(status_message, progress_0_to_1)
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    project_dir = Path(__file__).parent.parent
    temp_dir = None
    
    try:
        # Create temp directory
        temp_dir = Path(tempfile.mkdtemp(prefix="opf_update_"))
        zip_path = temp_dir / "update.zip"
        extract_dir = temp_dir / "extracted"
        
        if progress_callback:
            progress_callback("Downloading update...", 0.1)
        
        # Download ZIP
        req = Request(download_url, headers={
            "User-Agent": "PrivacyFilter-App",
        })
        with urlopen(req, timeout=120) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 8192
            
            with open(zip_path, "wb") as f:
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
        
        # Extract ZIP
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        
        # Find the extracted folder (usually starts with repo name)
        extracted_folders = list(extract_dir.iterdir())
        if not extracted_folders:
            return False, "Downloaded ZIP is empty"
        
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
            
            # Skip ZIP files and update temp
            if rel_path.suffix == ".zip" or ".update_temp" in rel_path.parts:
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
    """Restart the application."""
    import subprocess
    import time
    
    project_dir = Path(__file__).parent
    script = project_dir / "app_local.py"
    venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
    
    python = str(venv_python) if venv_python.exists() else sys.executable
    
    # Start new process detached
    subprocess.Popen(
        [python, str(script)],
        cwd=str(project_dir),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    
    # Exit current process
    time.sleep(0.5)
    os._exit(0)
