"""App and model update checks/installs."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/updates")
def api_updates() -> dict:
    """Check for app and model updates."""
    from server import updates

    return updates.get_updates()


@router.post("/api/updates/app")
def api_update_app() -> dict:
    """Install the latest app version and schedule a restart."""
    from server import updates

    return updates.install_app_update()


@router.post("/api/updates/model")
def api_update_model() -> dict:
    """Download the latest model checkpoint and reload it."""
    from server import updates

    return updates.install_model_update()
