"""App and model update orchestration for the FastAPI backend.

Thin wrappers over the existing ``app_update`` and ``model_update`` modules.
These functions are blocking (network / git / disk); call them from sync
FastAPI endpoints so they run in Starlette's threadpool.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from server import PROJECT_DIR, inference

logger = logging.getLogger("privacy_filter.updates")


def get_updates() -> dict:
    """Check for app and model updates and return both as plain dicts."""
    from app_update import check_for_app_update
    from model_update import check_for_model_update

    return {
        "app": asdict(check_for_app_update()),
        "model": asdict(check_for_model_update()),
    }


def restart_server() -> None:
    """Relaunch the backend as a detached process, then exit this one."""
    venv_python = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    python = str(venv_python) if venv_python.exists() else sys.executable
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    subprocess.Popen(
        [python, "-m", "server.main"],
        cwd=str(PROJECT_DIR),
        creationflags=creationflags,
        close_fds=True,
    )
    time.sleep(0.5)
    os._exit(0)


def install_app_update() -> dict:
    """Download/pull the latest app version and schedule a restart."""
    from app_update import check_for_app_update, download_and_install_update

    info = check_for_app_update()
    if not info.update_available:
        return {"status": "noop", "message": "No app update available."}

    if info.download_url:
        success, message = download_and_install_update(info.download_url)
    else:
        # No ZIP asset attached to the release: fall back to git pull + pip.
        try:
            result = subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=str(PROJECT_DIR),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                return {"status": "error", "message": f"git pull failed: {result.stderr}"}
            venv_pip = PROJECT_DIR / ".venv" / "Scripts" / "pip.exe"
            if venv_pip.exists():
                subprocess.run(
                    [str(venv_pip), "install", "-e", str(PROJECT_DIR / "privacy-filter")],
                    cwd=str(PROJECT_DIR),
                    capture_output=True,
                    timeout=120,
                )
            success, message = True, f"Updated to v{info.latest_version}"
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"Update failed: {exc}"}

    if not success:
        return {"status": "error", "message": message}

    # Restart shortly so this response can still be delivered.
    threading.Timer(2.0, restart_server).start()
    return {"status": "ok", "message": f"{message}. Restarting in 2 seconds…"}


def install_model_update() -> dict:
    """Download the latest model checkpoint and reload it."""
    from model_update import download_model_update

    success, message = download_model_update()
    if not success:
        return {"status": "error", "message": message}
    inference.reset_model()
    inference.get_model()  # warm reload
    return {"status": "ok", "message": message}
