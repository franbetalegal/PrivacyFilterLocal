"""Structural checks on the Linux and Windows launchers.

The macOS one has its own module (``test_macos_launcher.py``). These cover the
same ground for the other two, because the class of bug they catch is the one
this project has actually shipped: a packaging file that forgets a component,
with nothing failing until a user opens a document.

They are string checks, not executions — a launcher builds a virtualenv and
downloads PyTorch, which is not something a test run can do.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LINUX = REPO / "packaging" / "linux" / "run.sh"
WINDOWS_BUILDER = REPO / "build_portable.ps1"


def _linux() -> str:
    return LINUX.read_text(encoding="utf-8")


def _windows() -> str:
    return WINDOWS_BUILDER.read_text(encoding="utf-8")


# --- Linux -----------------------------------------------------------------

def test_linux_keeps_state_inside_the_folder():
    """Uninstall = 'delete the folder' only if every path stays under $DIR."""
    body = _linux()
    for key in (
        'OPF_CHECKPOINT="$DIR/model"',
        'HF_HOME="$DIR/cache/huggingface"',
        'PF_LOG_DIR="$DIR/logs"',
        'PF_NER_DIR="$DIR/ner-models"',
        'PF_HOST="127.0.0.1"',
    ):
        assert key in body, f"run.sh must export {key!r}"


def test_linux_uses_the_bundled_tesseract():
    body = _linux()
    assert '$DIR/tesseract/bin' in body, "the bundled binary must go on PATH"
    assert 'PF_TESSDATA_DIR="$DIR/tesseract/tessdata"' in body
    # Its libraries travel next to it; without this the binary will not load.
    assert 'LD_LIBRARY_PATH="$DIR/tesseract/lib' in body


def test_linux_prefers_its_own_python():
    body = _linux()
    assert 'BUNDLED_PY="$DIR/python/bin/python3"' in body
    assert 'if [ -n "${PYTHON:-}" ]' in body, "PYTHON= must still override"


def test_linux_uses_the_cpu_torch_wheel():
    """The CPU index is right here and wrong on macOS; keep them apart."""
    body = _linux()
    assert "download.pytorch.org/whl/cpu" in body


def test_linux_missing_tesseract_warns_without_aborting():
    body = _linux()
    start = body.index("if ! command -v tesseract")
    assert "exit 1" not in body[start:start + 600], (
        "OCR is optional; the app must still open for text-layer documents"
    )


# --- Windows ---------------------------------------------------------------

def test_windows_launcher_env_covers_every_component():
    body = _windows()
    for key in (
        'set "OPF_CHECKPOINT=%~dp0model"',
        'set "PF_NER_DIR=%~dp0ner-models"',
        'set "PF_TESSDATA_DIR=%~dp0tesseract\\tessdata"',
        'set "PATH=%~dp0tesseract\\bin;%PATH%"',
    ):
        assert key in body, f"the generated .bat env block must contain {key!r}"


def test_windows_uninstaller_removes_the_downloaded_models_and_tesseract():
    """Anything the build or the first run puts in the folder has to go, or an
    uninstall leaves gigabytes behind."""
    body = _windows()
    for folder in ("model", "ner-models", "tesseract", "python", "app"):
        assert f'rd /s /q "%~dp0{folder}"' in body, (
            f"uninstall.bat must remove {folder}/"
        )


def test_windows_build_bundles_tesseract_language_data():
    body = _windows()
    assert "$TESSDATA_LANGS" in body
    # The set has to match the PF_OCR_LANG default in server/pdf_ops.py, or the
    # app asks for a language the package does not carry and OCR fails.
    assert '@("spa", "cat", "eng")' in body


def test_windows_tesseract_languages_match_the_ocr_default():
    from server import pdf_ops  # noqa: PLC0415 — kept local to the assertion

    import inspect
    source = inspect.getsource(pdf_ops._ocr_page)
    assert '"PF_OCR_LANG", "spa+cat+eng"' in source, (
        "the OCR default changed; update $TESSDATA_LANGS in build_portable.ps1 "
        "and the language loops in release.yml to match"
    )
