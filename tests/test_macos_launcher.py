"""Smoke tests for the macOS launcher (packaging/macos/run.command).

We cannot exec the launcher in CI (it would create a venv and download PyTorch),
but a few structural checks catch the mistakes that would silently break a
user's first launch: missing exec bit, wrong shebang, or a regression where a
critical export or the Apple-Silicon-specific pip install line got removed.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "packaging" / "macos" / "run.command"


def test_launcher_file_exists():
    assert LAUNCHER.is_file(), f"missing {LAUNCHER}"


def test_launcher_is_executable():
    """Finder refuses to double-click a .command without the exec bit set."""
    mode = os.stat(LAUNCHER).st_mode
    assert mode & stat.S_IXUSR, "run.command must have the user exec bit"


def test_launcher_starts_with_bash_shebang():
    first = LAUNCHER.read_text(encoding="utf-8").splitlines()[0]
    # env-based shebang is the portable choice on macOS (bash lives in /bin,
    # but PATH-lookup works on both Intel and Apple Silicon layouts).
    assert first.startswith("#!") and "bash" in first, first


def test_launcher_passes_bash_n_syntax_check():
    """A shell syntax error would surface only when the user double-clicks."""
    r = subprocess.run(
        ["bash", "-n", str(LAUNCHER)],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, f"syntax errors in run.command:\n{r.stderr}"


def test_launcher_keeps_state_inside_the_folder():
    """Uninstall = 'delete the folder' only if every path stays under $DIR."""
    body = LAUNCHER.read_text(encoding="utf-8")
    for key in (
        "OPF_CHECKPOINT=\"$DIR/model\"",
        "HF_HOME=\"$DIR/cache/huggingface\"",
        "PF_LOG_DIR=\"$DIR/logs\"",
        "PF_DATA_DIR=\"$DIR/data\"",
        "PF_HOST=\"127.0.0.1\"",
    ):
        assert key in body, f"launcher must export {key!r}"


def test_launcher_uses_apple_silicon_torch_wheel():
    """No --index-url override — the default macOS wheel already includes MPS."""
    body = LAUNCHER.read_text(encoding="utf-8")
    assert "pip install torch\n" in body, (
        "run.command must install plain 'torch' on macOS (the default wheel "
        "supports MPS on Apple Silicon); the Linux --index-url pytorch/whl/cpu "
        "is wrong here."
    )
    assert "download.pytorch.org/whl/cpu" not in body, (
        "the CPU-only Linux wheel must NOT be forced on macOS"
    )


def test_launcher_opens_browser_with_macos_open():
    body = LAUNCHER.read_text(encoding="utf-8")
    assert "open \"http://localhost:$PORT\"" in body
    # xdg-open is Linux-only; if it crept in as executable code (not a comment)
    # the app would fail to open the browser silently. Strip comments before
    # checking so the module-header comment referencing xdg-open is allowed.
    code = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "xdg-open" not in code


def test_launcher_warns_about_tesseract_but_does_not_exit():
    """OCR is optional; a missing tesseract must warn, never abort startup."""
    body = LAUNCHER.read_text(encoding="utf-8")
    tess_block_start = body.index("if ! command -v tesseract")
    tess_block = body[tess_block_start : tess_block_start + 500]
    assert "exit 1" not in tess_block, (
        "the tesseract warning block must not exit; the app must still open"
    )


def test_launcher_uses_the_bundled_tesseract():
    """Shipped since 2.8.0, because 'install it yourself' left Windows and
    macOS users with an app that silently could not read scanned documents."""
    body = LAUNCHER.read_text(encoding="utf-8")
    assert '$DIR/tesseract/bin' in body, "the bundled binary must go on PATH"
    assert 'PF_TESSDATA_DIR="$DIR/tesseract/tessdata"' in body, (
        "server/pdf_ops.py reads PF_TESSDATA_DIR to pass --tessdata-dir; "
        "without it the bundled Tesseract finds no language data"
    )


def test_launcher_prefers_its_own_python():
    """No system Python required: the archive carries a relocatable CPython."""
    body = LAUNCHER.read_text(encoding="utf-8")
    assert 'BUNDLED_PY="$DIR/python/bin/python3"' in body
    # PYTHON= must still win, so a developer can point at their own.
    assert 'if [ -n "${PYTHON:-}" ]' in body


def test_launcher_clears_its_own_quarantine():
    """Extracted from a downloaded archive, every bundled binary is quarantined
    and macOS kills the unsigned ones on exec — with no message the user can
    act on."""
    body = LAUNCHER.read_text(encoding="utf-8")
    assert 'xattr -dr com.apple.quarantine "$DIR"' in body
    assert "PF_SKIP_QUARANTINE_CLEAR" in body, "there must be a way to opt out"
