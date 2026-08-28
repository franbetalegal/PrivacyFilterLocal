"""FastAPI application for Privacy Filter - Local.

Serves the JSON API under ``/api`` and the built React frontend under ``/``.
Run with:  uvicorn server.main:app --host 0.0.0.0 --port 7860

Routes live in :mod:`server.routes.*`, grouped by concern (core redaction,
standalone Markdown, dictionary, dataset/evaluation, updates, diagnostics);
this module only wires them together: app/middleware/exception-handler setup,
router registration, the static frontend mount and the CLI launcher.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Importing the package configures sys.path for ``opf`` / update modules.
from server import PROJECT_DIR
from server import inference, logging_setup

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server.routes import core, dataset, diagnostics, dictionary, markdown, updates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("privacy_filter")

logging_setup.setup_file_logging()

FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the preflight in the background so it doesn't block the server from
    # listening: it downloads the opf checkpoint and the spaCy NER models if
    # they are missing, then checks for a newer model version. Progress and the
    # per-component state are exposed via /api/health, and the UI stays on the
    # "Preparing" screen until everything is in place — an app that anonymises
    # with the name detector missing looks like it worked and did not.
    try:
        inference.start_background_download()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not start the startup preflight: %s", exc)
    yield


app = FastAPI(title="Privacy Filter - Local", version="2.0.0", lifespan=lifespan)

# Permissive CORS so the Vite dev server (different port) can call the API.
# In production the frontend is served from the same origin, so this is a no-op.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Log the full traceback and return a readable message to the UI."""
    ref = uuid.uuid4().hex[:8]
    logger.error(
        "Unhandled error [%s] on %s: %s",
        ref, request.url.path, exc, exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}", "ref": ref},
    )


for _router_module in (core, markdown, dictionary, dataset, updates, diagnostics):
    app.include_router(_router_module.router)


# --------------------------------------------------------------------------
#  Static frontend (mounted last so /api/* takes precedence)
# --------------------------------------------------------------------------
class _NoStoreIndex(StaticFiles):
    """Serve the built frontend, but never let the browser cache the entry page.

    Asset filenames are content-hashed by Vite, so they are safe to cache
    forever. ``index.html`` is not hashed, and it is the file that names which
    bundle to load: a cached copy keeps requesting the previous build. Rebuild
    the frontend and the browser happily shows the old interface, which reads
    exactly like the change never landed.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path in ("", ".", "/", "index.html"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


def _mount_frontend() -> None:
    if FRONTEND_DIST.is_dir():
        app.mount("/", _NoStoreIndex(directory=str(FRONTEND_DIST), html=True),
                  name="frontend")
        logger.info("Serving frontend from %s", FRONTEND_DIST)
    else:
        logger.warning(
            "Frontend build not found at %s. Run `npm run build` in frontend/. "
            "The API is still available under /api.", FRONTEND_DIST,
        )

        @app.get("/")
        def _no_frontend() -> dict:
            return {
                "message": "Frontend not built. Run `npm run build` in frontend/.",
                "api": "/api",
            }


_mount_frontend()


def _ensure_std_streams() -> None:
    """Under pythonw.exe (no console) sys.stdout/stderr are None, which makes
    uvicorn's stream logging crash on startup. Point them at a file so the
    server runs windowless and any output is captured."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log_dir = Path(os.environ.get("PF_LOG_DIR") or (PROJECT_DIR / "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "server-stdout.log", "a", buffering=1, encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not set up std streams: %s", exc)


def _port_is_free(host: str, port: int) -> bool:
    """Whether ``port`` can be bound on ``host`` right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, start: int, attempts: int = 11) -> int | None:
    """First free port at or after ``start``, or None if none is.

    The port has to be chosen before uvicorn starts, not after it fails: uvicorn
    calls ``sys.exit(1)`` from ``Config.bind_socket`` when the bind fails, and a
    SystemExit walks straight through an ``except OSError`` retry loop.
    """
    for port in range(start, start + attempts):
        if _port_is_free(host, port):
            return port
        logger.warning("Port %d in use, trying %d...", port, port + 1)
    return None


def main() -> None:
    """Launch uvicorn on the first free port at or after PF_PORT.

    Host and starting port can be overridden with PF_HOST / PF_PORT (the
    portable build sets PF_HOST=127.0.0.1 to stay local-only). The port actually
    used is written back to PF_PORT so a restart (see
    :func:`server.updates.restart_server`, which re-execs this module) comes back
    on the same port the user's browser is already pointing at.
    """
    import uvicorn

    _ensure_std_streams()

    host = os.environ.get("PF_HOST", "0.0.0.0")
    start_port = int(os.environ.get("PF_PORT", "7860"))
    port = _pick_port(host, start_port)
    if port is None:
        logger.error(
            "No free port between %d and %d; is another instance running?",
            start_port, start_port + 10,
        )
        raise SystemExit(1)
    os.environ["PF_PORT"] = str(port)
    try:
        logger.info("Open http://localhost:%d", port)
        # log_config=None: don't let uvicorn install its own stdout/stderr
        # stream handlers (they break under pythonw); our root file handler
        # already captures uvicorn's logs via propagation.
        uvicorn.run(app, host=host, port=port, workers=1, log_config=None)
    except SystemExit:
        # uvicorn exits this way when it cannot bind (lost the race with another
        # process between the probe and the real bind). Say so instead of dying
        # silently.
        logger.error("Server failed to start on port %d", port)
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Server crashed on startup: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    main()
