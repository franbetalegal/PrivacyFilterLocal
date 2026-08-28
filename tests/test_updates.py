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


def test_dependency_reinstall_failure_fails_the_update(monkeypatch, tmp_path):
    """New sources against a stale venv is a failed update, not a footnote.

    This used to return success with the pip error appended to the message, so
    the app said "updated" while running new code against the previous
    version's packages — the state that let three releases ship without the
    spaCy NER models and nothing say so.
    """
    (tmp_path / "privacy-filter").mkdir()
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)

    class _Failed:
        returncode = 1
        stderr = "pip exploded"

    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **k: _Failed())

    ok, message = updates._reinstall_dependencies("Updated to v2.6.1")

    assert not ok
    assert "Updated to v2.6.1" in message
    assert "pip exploded" in message


def test_dependency_reinstall_can_skip_the_editable_package(monkeypatch, tmp_path):
    """The git-checkout branch must not undo its own editable install.

    It reinstalls ``privacy-filter`` with ``-e`` and then asks for the pinned
    requirements; installing the package again as a regular wheel would replace
    the editable install behind its back.
    """
    (tmp_path / "privacy-filter").mkdir()
    (tmp_path / "requirements-server.txt").write_text("fastapi\n", encoding="utf-8")
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)
    calls: list[list[str]] = []

    class _Ok:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(
        updates.subprocess, "run",
        lambda command, **kwargs: (calls.append(command), _Ok())[1],
    )

    ok, _ = updates._reinstall_dependencies("Updated", include_package=False)

    assert ok
    assert len(calls) == 1
    assert "-r" in calls[0]


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


def test_successful_update_flags_the_restart_instead_of_describing_it(
    monkeypatch, tmp_path
):
    """The backend signals the restart with a flag, not with English prose.

    The message the frontend shows is Spanish and belongs to the frontend (see
    the message contract in CLAUDE.md); the backend used to append
    "Restarting in 2 seconds…" and the UI rendered it raw.
    """
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(
        "app_update.check_for_app_update",
        lambda: AppUpdateInfo(
            update_available=True,
            current_version="2.6.0",
            latest_version="2.6.1",
            download_url="https://example.invalid/opf-2.6.1.tar.gz",
        ),
    )
    monkeypatch.setattr(
        "app_update.download_and_install_update",
        lambda url: (True, "Updated from 2.6.0 to 2.6.1"),
    )
    monkeypatch.setattr(updates, "_reinstall_dependencies", lambda m: (True, m))
    monkeypatch.setattr(updates, "_rebuild_frontend", lambda: (True, "no frontend"))

    scheduled: list[object] = []

    class _Timer:
        def __init__(self, delay, function):
            self.delay = delay
            self.function = function

        def start(self):
            scheduled.append((self.delay, self.function))

    monkeypatch.setattr(updates.threading, "Timer", _Timer)

    result = updates.install_app_update()

    assert result == {
        "status": "ok",
        "message": "Updated from 2.6.0 to 2.6.1",
        "restarting": True,
    }
    assert scheduled and scheduled[0][1] is updates.restart_server


def test_restart_replaces_the_current_process(monkeypatch, tmp_path):
    """Restart must re-exec, not spawn a child and die.

    Spawning meant the child inherited the launcher's process group on
    macOS/Linux, so the SIGHUP from the parent's exit killed it and the server
    never came back. Replacing the process image keeps the PID, the session and
    the terminal window, and releases the listening socket via close-on-exec.
    """
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(updates, "_venv_python", lambda: "/venv/bin/python")
    monkeypatch.setattr(updates.os, "chdir", lambda path: None)

    def _no_spawning(*args, **kwargs):
        raise AssertionError("restart must not spawn a separate process")

    monkeypatch.setattr(updates.subprocess, "Popen", _no_spawning)

    execs: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        updates.os, "execv", lambda path, argv: execs.append((path, argv))
    )

    updates.restart_server()

    assert execs == [("/venv/bin/python", ["/venv/bin/python", "-m", "server.main"])]


def test_restart_falls_back_to_a_detached_process_when_exec_fails(
    monkeypatch, tmp_path
):
    """Windows is where execv can refuse; the server must still come back.

    The fallback needs its own session on POSIX too, otherwise it reproduces the
    original bug.
    """
    monkeypatch.setattr(updates, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(updates, "_venv_python", lambda: "/venv/bin/python")
    monkeypatch.setattr(updates.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        updates.os, "execv",
        lambda path, argv: (_ for _ in ()).throw(OSError("no exec here")),
    )
    monkeypatch.setattr(updates.time, "sleep", lambda seconds: None)

    spawned: list[dict] = []
    monkeypatch.setattr(
        updates.subprocess, "Popen",
        lambda command, **kwargs: spawned.append({"command": command, **kwargs}),
    )

    exits: list[int] = []
    monkeypatch.setattr(updates.os, "_exit", lambda code: exits.append(code))

    updates.restart_server()

    assert exits == [0]
    assert spawned[0]["command"] == ["/venv/bin/python", "-m", "server.main"]
    if updates.os.name != "nt":
        assert spawned[0]["start_new_session"] is True
