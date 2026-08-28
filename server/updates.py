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

from app_update import GITHUB_REPO
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
    # An install unpacked from a release archive carries frontend/dist prebuilt
    # and no sources, so there is nothing to build and pnpm would fail on the
    # missing package.json. Only a checkout has one.
    if not (frontend / "package.json").is_file():
        return True, "frontend already built (no sources to build from)"
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


def _spawn_detached_and_exit(python: str) -> None:
    """Fallback restart: launch a separate backend process and exit this one.

    Only reached when ``os.execv`` is unavailable or fails. ``start_new_session``
    matters on POSIX: without it the child inherits the process group of the
    Terminal that ran run.command, and the SIGHUP raised when this process dies
    takes the child with it — which is exactly how the old restart failed.
    """
    creationflags = 0
    kwargs: dict = {}
    if os.name == "nt":
        creationflags = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [python, "-m", "server.main"],
        cwd=str(PROJECT_DIR),
        creationflags=creationflags,
        close_fds=True,
        **kwargs,
    )
    time.sleep(0.5)
    os._exit(0)


def restart_server() -> None:
    """Restart the backend in place by replacing this process image.

    ``os.execv`` keeps the PID, the session and the launcher's terminal window,
    so nothing is orphaned and no supervisor is needed: it works the same under
    run.command, run.sh, ``python -m server.main`` and the Windows portable.
    Python marks its file descriptors close-on-exec (PEP 446), so uvicorn's
    listening socket is released by the exec itself and the new process can bind
    the same port — which ``server.main`` pinned in ``PF_PORT``.

    Logged before exec'ing: past this call the process is gone and would leave
    no trace in the log file otherwise.
    """
    python = _venv_python()
    logger.info("Restarting server: exec %s -m server.main", python)
    try:
        os.chdir(PROJECT_DIR)
        os.execv(python, [python, "-m", "server.main"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("exec restart failed (%s); spawning a new process", exc)
        _spawn_detached_and_exit(python)


def _reinstall_dependencies(
    message: str, *, include_package: bool = True
) -> tuple[bool, str]:
    """Reinstall the bundled ``opf`` package and pinned server requirements.

    An archive install replaces the files under ``privacy-filter/`` and the
    pins in ``requirements-server.txt``, but the venv still holds what the
    previous version installed. Without this the update ships new sources and
    keeps running the old wheel.

    A failure here is an error, not a note. It leaves new sources on disk
    running against the previous version's packages, which is the shape of
    problem that hides for months: the app reports "updated successfully" and
    then behaves like neither version. The user needs to know the update did
    not complete so they can reinstall.
    """
    venv_py = _venv_python()
    package = PROJECT_DIR / "privacy-filter"
    requirements = PROJECT_DIR / "requirements-server.txt"
    steps: list[list[str]] = []
    # ``include_package=False`` is for the git-checkout branch, which already
    # reinstalled ``privacy-filter`` in editable mode: installing it again as a
    # regular package would silently undo that.
    if include_package and package.is_dir():
        steps.append([venv_py, "-m", "pip", "install", str(package)])
    if requirements.is_file():
        steps.append([venv_py, "-m", "pip", "install", "-r", str(requirements)])
    for step in steps:
        try:
            result = subprocess.run(
                step, cwd=str(PROJECT_DIR), capture_output=True, text=True,
                timeout=900,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Dependency reinstall failed: %s", exc)
            return False, f"{message}, but the dependency reinstall failed: {exc}"
        if result.returncode != 0:
            tail = (result.stderr or "")[-500:]
            logger.error("Dependency reinstall failed: %s", tail)
            return False, f"{message}, but the dependency reinstall failed: {tail}"
    return True, message


def install_app_update() -> dict:
    """Download/pull the latest app version and schedule a restart."""
    from app_update import check_for_app_update, download_and_install_update

    info = check_for_app_update()
    if not info.update_available:
        return {"status": "noop", "message": "No app update available."}

    if info.download_url:
        success, message = download_and_install_update(info.download_url)
        if success:
            success, message = _reinstall_dependencies(message)
    elif not (PROJECT_DIR / ".git").exists():
        # No archive for this platform and nothing to pull: an install unpacked
        # from a release archive on a platform we do not build one for, or a
        # folder someone copied out of a checkout. Say what to do instead of
        # failing with a git error about a directory that is not a repository.
        return {
            "status": "error",
            "message": (
                f"Version {info.latest_version} is available but cannot be "
                "installed automatically on this platform. Download it from "
                f"https://github.com/{GITHUB_REPO}/releases/latest and unpack "
                "it over this folder."
            ),
        }
    else:
        # A checkout: pull the new code instead of unpacking an archive over it.
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
            # A pull can change the pins too. This branch used to stop at the
            # editable opf install, so a checkout that updated from inside the
            # app kept the previous version's dependencies indefinitely — new
            # code against old packages, with nothing saying so.
            requirements = PROJECT_DIR / "requirements-server.txt"
            if venv_py != sys.executable and requirements.is_file():
                success, message = _reinstall_dependencies(
                    message, include_package=False
                )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"Update failed: {exc}"}

    if not success:
        return {"status": "error", "message": message}

    # Rebuild the frontend so the served UI matches the updated code.
    built_ok, built_msg = _rebuild_frontend()
    if not built_ok:
        logger.warning("%s", built_msg)
        message = f"{message} (warning: {built_msg})"

    # Restart shortly so this response can still be delivered. The client is
    # told a restart is coming via the flag, not via prose: the Spanish sentence
    # belongs to the frontend (see the message contract in CLAUDE.md).
    threading.Timer(2.0, restart_server).start()
    return {"status": "ok", "message": message, "restarting": True}


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
