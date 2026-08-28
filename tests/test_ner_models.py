"""Tests for the runtime download of the spaCy NER models.

Every test here is offline. The fixture builds a wheel with the same shape the
real ones have — ``<name>/<name>-<version>/`` around a model directory — so the
unpacking, validation and atomic-replace logic is exercised without moving
600 MB, and ``spacy.load`` is stubbed because opening a real pipeline is what
the release smoke test is for.

Why this module has its own tests at all: the models were missing from every
published build for three releases and nothing failed. The install path is now
load-bearing, so it gets the coverage the comment in requirements-server.txt
never provided.
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import ner_models


MODEL = "es_core_news_lg"
VERSION = "3.8.0"


def _wheel_bytes(name: str = MODEL, version: str = VERSION, *, complete: bool = True) -> bytes:
    """A minimal wheel with the layout Explosion publishes."""
    prefix = f"{name}/{name}-{version}/"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{name}/__init__.py", "# package wrapper\n")
        archive.writestr(
            prefix + "meta.json",
            json.dumps({"lang": name[:2], "name": name[3:], "version": version}),
        )
        if complete:
            archive.writestr(prefix + "config.cfg", "[nlp]\nlang = es\n")
        archive.writestr(prefix + "ner/model", "weights")
    return buffer.getvalue()


@pytest.fixture
def managed_dir(tmp_path, monkeypatch):
    """Point the module at a throwaway models directory."""
    target = tmp_path / "ner-models"
    monkeypatch.setenv("PF_NER_DIR", str(target))
    return target


@pytest.fixture
def offline_install(monkeypatch):
    """Serve a wheel from memory and accept any model spaCy is asked to load."""
    served = {"bytes": _wheel_bytes(), "urls": []}

    class _Response:
        def __init__(self, payload: bytes):
            self._stream = io.BytesIO(payload)
            self.headers = {"Content-Length": str(len(payload))}

        def read(self, size=-1):
            return self._stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _urlopen(request, **kwargs):
        url = getattr(request, "full_url", request)
        served["urls"].append(url)
        return _Response(served["bytes"])

    monkeypatch.setattr(ner_models, "urlopen", _urlopen)

    fake_spacy = type(sys)("spacy")
    fake_spacy.__version__ = "3.8.13"
    fake_spacy.about = type(sys)("about")
    fake_spacy.about.__compatibility__ = "https://example.invalid/compatibility.json"
    fake_spacy.about.__download_url__ = "https://example.invalid/releases"
    fake_spacy.load = lambda path, **kwargs: object()
    monkeypatch.setitem(sys.modules, "spacy", fake_spacy)
    return served


def test_install_unpacks_only_the_model_directory(managed_dir, offline_install):
    ok, message = ner_models.install(MODEL, version=VERSION)

    assert ok, message
    installed = managed_dir / MODEL
    assert (installed / "config.cfg").is_file()
    assert (installed / "meta.json").is_file()
    assert (installed / "ner" / "model").is_file()
    # The pip package wrapper is not part of a loadable model directory.
    assert not (installed / "__init__.py").exists()
    assert ner_models.installed_version(MODEL) == VERSION


def test_install_asks_for_the_published_wheel_url(managed_dir, offline_install):
    ner_models.install(MODEL, version=VERSION)

    assert offline_install["urls"] == [
        f"https://example.invalid/releases/{MODEL}-{VERSION}/"
        f"{MODEL}-{VERSION}-py3-none-any.whl"
    ]


def test_a_previous_version_survives_a_failed_install(managed_dir, offline_install):
    """The whole point of staging: a bad download must not cost a good model."""
    assert ner_models.install(MODEL, version=VERSION)[0]
    (managed_dir / MODEL / "marker").write_text("original", encoding="utf-8")

    # A wheel whose model directory has no config.cfg fails validation.
    offline_install["bytes"] = _wheel_bytes(version="3.8.1", complete=False)
    ok, message = ner_models.install(MODEL, version="3.8.1")

    assert not ok
    assert "config.cfg" in message
    assert (managed_dir / MODEL / "marker").read_text(encoding="utf-8") == "original"
    assert ner_models.installed_version(MODEL) == VERSION


def test_a_version_mismatch_is_refused(managed_dir, offline_install):
    """A wheel that does not contain what was asked for is not installed."""
    ok, message = ner_models.install(MODEL, version="3.9.9")

    assert not ok
    assert not (managed_dir / MODEL).exists()
    # It never even found the directory the requested version should live in.
    assert "3.9.9" in message


def test_no_staging_directory_is_left_behind(managed_dir, offline_install):
    offline_install["bytes"] = _wheel_bytes(complete=False)
    ner_models.install(MODEL, version=VERSION)

    leftovers = list(managed_dir.iterdir()) if managed_dir.exists() else []
    assert leftovers == []


def test_an_incomplete_directory_does_not_count_as_installed(managed_dir, monkeypatch):
    """A half-written download must be treated as absent, not as a model."""
    # Ignore any model pip-installed on the machine running the tests; this is
    # about what the managed directory contains.
    monkeypatch.setattr(ner_models, "is_pip_installed", lambda name: False)
    (managed_dir / MODEL).mkdir(parents=True)
    (managed_dir / MODEL / "meta.json").write_text("{}", encoding="utf-8")

    assert not ner_models.is_installed(MODEL)
    assert not ner_models.is_present(MODEL)
    assert MODEL in ner_models.missing()


def test_latest_compatible_reads_the_table_for_the_installed_spacy(monkeypatch):
    table = {"spacy": {"3.8": {MODEL: ["3.8.0", "3.7.0"]}, "3.7": {MODEL: ["3.7.0"]}}}
    monkeypatch.setattr(ner_models, "_spacy_minor", lambda: "3.8")

    assert ner_models.latest_compatible(MODEL, table=table) == "3.8.0"


def test_latest_compatible_is_none_when_the_check_cannot_run(monkeypatch):
    """Offline is not an error here: it means keep what is installed."""
    def _boom():
        raise OSError("no network")

    monkeypatch.setattr(ner_models, "_compatibility_table", _boom)

    assert ner_models.latest_compatible(MODEL) is None


def test_a_pip_installed_model_counts_as_present(monkeypatch):
    """A development machine uses `python -m spacy download`; don't re-fetch."""
    monkeypatch.setattr(ner_models, "is_installed", lambda name: False)
    monkeypatch.setattr(ner_models, "is_pip_installed", lambda name: True)

    assert ner_models.is_present(MODEL)
    assert ner_models.missing() == []


def test_the_configured_model_list_is_never_empty():
    """The guard for the original bug: no models configured means no names."""
    assert ner_models.MODEL_NAMES
    assert all(isinstance(name, str) and name for name in ner_models.MODEL_NAMES)
