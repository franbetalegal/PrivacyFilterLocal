"""Tests for the app-update orchestration in server.updates.

The update path is hard to exercise on a real install (it downloads, unpacks
over the running files and restarts), so these cover the decisions it makes
before any of that: which install shape it is looking at, and what it tells the
user when it cannot help.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_update import AppUpdateInfo
from server import updates


def test_reports_where_to_download_when_it_cannot_install_itself(monkeypatch, tmp_path):
    """An unpacked release archive is not a git repository.

    With no asset for this platform the installer used to run `git pull` there
    anyway and surface git's own complaint about a directory that is not a
    repository. The user's actual options belong in the message instead.
    """
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(
        "app_update.check_for_app_update",
        lambda: AppUpdateInfo(
            update_available=True, current_version="2.6.0", latest_version="2.6.1",
        ),
    )

    result = updates.install_app_update()

    assert result["status"] == "error"
    assert "2.6.1" in result["message"]
    assert "releases/latest" in result["message"]
    assert "git pull" not in result["message"]


def test_says_nothing_to_do_when_already_current(monkeypatch):
    monkeypatch.setattr(
        "app_update.check_for_app_update",
        lambda: AppUpdateInfo(update_available=False, current_version="2.6.1"),
    )
    assert updates.install_app_update()["status"] == "noop"


def test_frontend_rebuild_skipped_when_the_install_has_no_sources(monkeypatch, tmp_path):
    """A release archive ships frontend/dist prebuilt and no package.json.

    Running pnpm there fails on the missing manifest, which used to append a
    spurious build warning to an otherwise clean update.
    """
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)

    ok, message = updates._rebuild_frontend()

    assert ok
    assert "already built" in message


def test_frontend_rebuild_attempted_when_sources_are_present(monkeypatch, tmp_path):
    """A checkout has package.json, so the build must still be attempted."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(updates.shutil, "which", lambda name: None)

    ok, message = updates._rebuild_frontend()

    # No Node on this host: skipped, but for the toolchain, not the manifest.
    assert ok
    assert "Node/pnpm" in message


def test_dependency_reinstall_warns_without_failing_the_update(monkeypatch, tmp_path):
    """New sources with a stale venv is a warning, not a failed update.

    The files are already in place by then; refusing the update would leave the
    install in the same state while reporting failure.
    """
    (tmp_path / "privacy-filter").mkdir()
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)

    class _Failed:
        returncode = 1
        stderr = "pip exploded"

    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **k: _Failed())

    ok, message = updates._reinstall_dependencies("Updated to v2.6.1")

    assert ok
    assert "Updated to v2.6.1" in message
    assert "pip exploded" in message


def test_dependency_reinstall_installs_package_then_requirements(monkeypatch, tmp_path):
    (tmp_path / "privacy-filter").mkdir()
    (tmp_path / "requirements-server.txt").write_text("fastapi\n", encoding="utf-8")
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)
    calls: list[list[str]] = []

    class _Ok:
        returncode = 0
        stderr = ""

    def _run(command, **kwargs):
        calls.append(command)
        return _Ok()

    monkeypatch.setattr(updates.subprocess, "run", _run)

    ok, message = updates._reinstall_dependencies("Updated to v2.6.1")

    assert ok
    assert message == "Updated to v2.6.1"
    assert len(calls) == 2
    assert str(tmp_path / "privacy-filter") in calls[0]
    assert "-r" in calls[1]
