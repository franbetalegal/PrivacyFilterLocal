"""App and model update orchestration for the FastAPI backend.

Thin wrappers over the existing ``app_update`` and ``model_update`` modules.
These functions are blocking (network / git / disk); call them from sync
FastAPI endpoints so they run in Starlette's threadpool.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from server import PROJECT_DIR, inference

logger = logging.getLogger("privacy_filter.updates")


def _venv_python() -> str:
    """Path to the project's venv Python, or the current interpreter.

    The venv layout differs per OS: ``Scripts\\python.exe`` on Windows,
    ``bin/python`` on Linux/macOS. Falls back to ``sys.executable`` (e.g. the
    embedded Python in the portable build) when no venv is present.
    """
    if os.name == "nt":
        candidate = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = PROJECT_DIR / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def get_updates() -> dict:
    """Check for app and model updates and return both as plain dicts."""
    from app_update import check_for_app_update
    from model_update import check_for_model_update

    return {
        "app": asdict(check_for_app_update()),
        "model": asdict(check_for_model_update()),
    }


def _rebuild_frontend() -> tuple[bool, str]:
    """Rebuild the React frontend after an app update (best effort).

    Uses pnpm via corepack, matching install.ps1. On failure the previous
    build under frontend/dist is left in place.
    """
    frontend = PROJECT_DIR / "frontend"
    if not frontend.is_dir():
        return True, "no frontend to build"
    # Node/pnpm are build-time only and usually absent on end-user machines
    # (especially Linux). Skip gracefully rather than fail the whole update;
    # the previously built frontend/dist stays in place.
    if shutil.which("corepack") is None and shutil.which("pnpm") is None:
        return True, "frontend rebuild skipped (Node/pnpm not installed)"
    env = {**os.environ, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"}
    try:
        subprocess.run(
            ["corepack", "pnpm", "install", "--frozen-lockfile"],
            cwd=str(frontend), capture_output=True, timeout=600, env=env,
        )
        result = subprocess.run(
            ["corepack", "pnpm", "run", "build"],
            cwd=str(frontend), capture_output=True, text=True, timeout=600, env=env,
        )
        if result.returncode != 0:
            return False, f"frontend build failed: {result.stderr[-500:]}"
        return True, "frontend rebuilt"
    except Exception as exc:  # noqa: BLE001
        return False, f"frontend rebuild error: {exc}"


def restart_server() -> None:
    """Relaunch the backend as a detached process, then exit this one."""
    python = _venv_python()
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
            # Reinstall the editable opf package into the project's venv, if any
            # (skipped for the portable build, which has no .venv).
            venv_py = _venv_python()
            if venv_py != sys.executable:
                subprocess.run(
                    [venv_py, "-m", "pip", "install", "-e",
                     str(PROJECT_DIR / "privacy-filter")],
                    cwd=str(PROJECT_DIR),
                    capture_output=True,
                    timeout=120,
                )
            success, message = True, f"Updated to v{info.latest_version}"
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"Update failed: {exc}"}

    if not success:
        return {"status": "error", "message": message}

    # Rebuild the frontend so the served UI matches the updated code.
    built_ok, built_msg = _rebuild_frontend()
    if not built_ok:
        logger.warning("%s", built_msg)
        message = f"{message} (warning: {built_msg})"

    # Restart shortly so this response can still be delivered.
    threading.Timer(2.0, restart_server).start()
    return {"status": "ok", "message": f"{message}. Restarting in 2 seconds…"}


def install_model_update() -> dict:
    """Update the model checkpoint, downloading only when actually needed.

    Behaviour:
    - If no valid checkpoint is present yet, download it (first install).
    - If a checkpoint already exists, check HuggingFace first and skip the
      (large) download when the local model is already up to date.
    - If the update check cannot reach HuggingFace, do NOT download; report the
      network error instead so we never re-download blindly.
    """
    from model_update import check_for_model_update, download_model_update

    # Only the date-based "is an update needed?" check applies when we already
    # have a working model. A missing/partial checkpoint is an install, not an
    # update, so we always download in that case.
    if inference._checkpoint_is_valid(inference.checkpoint_dir()):
        info = check_for_model_update()
        if info.error:
            return {
                "status": "error",
                "message": (
                    f"Could not check for a model update: {info.error}. "
                    "Nothing was downloaded."
                ),
            }
        if not info.update_available:
            current = info.current_date or info.latest_date or "unknown"
            return {
                "status": "noop",
                "message": f"The model is already up to date (version {current}).",
            }

    success, message = download_model_update()
    if not success:
        return {"status": "error", "message": message}
    inference.reset_model()
    inference.get_model()  # warm reload
    return {"status": "ok", "message": message}
