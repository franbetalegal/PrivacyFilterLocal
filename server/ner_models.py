"""Download, validate and keep up to date the spaCy NER models.

Why this module exists
    The models are the difference between finding a person's name and missing
    it. Measured on a real tax document, the pipeline finds 18 ``NOMBRE`` spans
    with them and 5 without — every name written in caps is lost, because the
    ``opf`` model is documented as primarily English. Until now they were only
    mentioned in a comment in ``requirements-server.txt``, so no launcher, no
    build script and no CI job ever installed them, and every published release
    anonymised documents with that layer silently missing.

    Bundling them into the installers would add ~1.2 GB to a Windows build that
    bakes its virtualenv at build time. Instead they are fetched at runtime,
    exactly like the ``opf`` checkpoint is (see :mod:`model_update`), and the
    startup preflight in :mod:`server.inference` refuses to let the app run
    without them.

Why not pip
    A model is data, not code. Installing a wheel into the virtualenv of a
    running process is the fragile path — it is what already misbehaves in
    :func:`server.updates._reinstall_dependencies` — and it puts a 600 MB
    artifact somewhere "delete the folder to uninstall" does not reach. The
    wheel published by Explosion is a plain zip whose inner
    ``<name>/<name>-<version>/`` directory is exactly what ``spacy.load``
    accepts as a path, so unpacking it into the app folder is enough.

Where the URLs come from
    spaCy publishes both endpoints itself, so nothing here is hardcoded
    guesswork: ``spacy.about.__compatibility__`` is the table mapping a spaCy
    minor version to the model versions built for it, and
    ``spacy.about.__download_url__`` is the release base. Resolving through the
    table is what ``python -m spacy download`` does, and it is why a model
    upgrade can never land a version the installed spaCy cannot open.

Layout on disk::

    <models_dir()>/es_core_news_lg/{config.cfg, meta.json, ner/, ...}

The directory is the loadable model itself, so the installed version is read
back from its ``meta.json``. Installs are atomic: the download is unpacked and
validated in a temporary sibling and only then moved into place, so a failure
at any point leaves the previous working model untouched.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional
from urllib.request import Request, urlopen

# Shared with the app and model update checks so all three verify against the
# same CA bundle; see app_update.ssl_context for why the platform default is
# not enough on a python.org framework Python.
from app_update import ssl_context
from server import PROJECT_DIR

logger = logging.getLogger("privacy_filter.ner_models")

ProgressCallback = Callable[[str, float], None]


# Model list resolution: PF_NER_MODELS wins; fall back to the legacy
# PF_NER_MODEL; fall back to the built-in default (Spanish + Catalan). This
# lives here rather than in ner_es because the downloader and the loader must
# agree on the list — two copies is how a model ends up fetched but not loaded.
_MODELS_ENV = os.environ.get("PF_NER_MODELS")
_LEGACY_MODEL = os.environ.get("PF_NER_MODEL")
if _MODELS_ENV:
    MODEL_NAMES: tuple[str, ...] = tuple(
        m.strip() for m in _MODELS_ENV.split(",") if m.strip()
    )
elif _LEGACY_MODEL:
    MODEL_NAMES = (_LEGACY_MODEL.strip(),)
else:
    MODEL_NAMES = ("es_core_news_lg", "ca_core_news_lg")


def models_dir() -> Path:
    """Directory holding the managed models.

    ``PF_NER_DIR`` overrides it; the default sits inside the project folder so
    the portable builds keep their "everything lives here, delete the folder to
    uninstall" promise without any launcher having to export a variable.
    """
    override = os.environ.get("PF_NER_DIR")
    if override:
        return Path(override).expanduser()
    return PROJECT_DIR / "ner-models"


def model_dir(name: str) -> Path:
    """Where ``name`` is installed (whether or not it is actually there)."""
    return models_dir() / name


def _read_meta(directory: Path) -> Optional[dict]:
    try:
        with (directory / "meta.json").open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def is_installed(name: str) -> bool:
    """True when ``name`` is present and structurally complete.

    ``config.cfg`` is what ``spacy.load`` reads first and ``meta.json`` is what
    carries the version, so a directory missing either is a half-written
    download rather than a model.
    """
    directory = model_dir(name)
    return (directory / "config.cfg").is_file() and (directory / "meta.json").is_file()


def installed_version(name: str) -> Optional[str]:
    """Version of the usable copy of ``name``, managed or pip, if any.

    The managed copy wins because that is the one :mod:`server.ner_es` loads.
    A pip-installed model keeps its ``meta.json`` at the package root, so a
    development machine reports a real version too instead of a blank in the
    diagnostics bundle.
    """
    if is_installed(name):
        return (_read_meta(model_dir(name)) or {}).get("version")
    if is_pip_installed(name):
        import importlib.util

        spec = importlib.util.find_spec(name)
        origin = getattr(spec, "origin", None)
        if origin:
            return (_read_meta(Path(origin).parent) or {}).get("version")
    return None


def is_pip_installed(name: str) -> bool:
    """True when ``name`` is importable as a package.

    That is how a development machine gets a model (``python -m spacy
    download``), and it counts as present: re-downloading 600 MB into the app
    folder to duplicate what is already loadable would be wasteful and
    confusing.
    """
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def is_present(name: str) -> bool:
    """True when ``name`` is usable, from either the managed copy or pip."""
    return is_installed(name) or is_pip_installed(name)


def missing() -> list[str]:
    """Configured models that are not usable from either source."""
    return [name for name in MODEL_NAMES if not is_present(name)]


def _spacy_minor() -> str:
    """The ``major.minor`` key the compatibility table is indexed by."""
    import spacy

    return ".".join(spacy.__version__.split(".")[:2])


def _compatibility_table() -> dict:
    import spacy

    request = Request(
        spacy.about.__compatibility__,
        headers={"Accept": "application/json", "User-Agent": "PrivacyFilter-App"},
    )
    with urlopen(request, timeout=15, context=ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_compatible(name: str, table: Optional[dict] = None) -> Optional[str]:
    """Newest version of ``name`` built for the installed spaCy, or ``None``.

    ``table`` lets a caller resolve several models from one fetch. Returns
    ``None`` on any network or parsing failure: not knowing about a newer
    version is a reason to keep the current one, never to fail a startup.
    """
    try:
        data = table if table is not None else _compatibility_table()
        versions = data["spacy"][_spacy_minor()][name]
    except Exception as exc:  # noqa: BLE001 — a probe must never be fatal
        logger.debug("Could not resolve a compatible version for %s: %s", name, exc)
        return None
    # The table lists newest first.
    return versions[0] if versions else None


def _wheel_url(name: str, version: str) -> str:
    import spacy

    return (
        f"{spacy.about.__download_url__}/{name}-{version}/"
        f"{name}-{version}-py3-none-any.whl"
    )


def _download_wheel(
    url: str, target: Path, progress_callback: Optional[ProgressCallback], name: str
) -> None:
    """Stream the wheel to ``target``, reporting progress when the size is known."""
    request = Request(url, headers={"User-Agent": "PrivacyFilter-App"})
    with urlopen(request, timeout=60, context=ssl_context()) as response:
        total = int(response.headers.get("Content-Length") or 0)
        read = 0
        with target.open("wb") as fh:
            while True:
                block = response.read(1024 * 512)
                if not block:
                    break
                fh.write(block)
                read += len(block)
                if progress_callback and total:
                    # 0.05..0.75 of this model's share: unpack and validate own
                    # the rest, and they are not instant on a 600 MB model.
                    fraction = 0.05 + 0.70 * (read / total)
                    progress_callback(f"Downloading {name}", fraction)


def _unpack_wheel(wheel: Path, name: str, version: str, destination: Path) -> None:
    """Extract the model directory out of the wheel into ``destination``.

    The wheel holds ``<name>/<name>-<version>/`` — a package wrapper around the
    model directory. Only that inner tree is wanted; the wrapper's
    ``__init__.py`` and the ``.dist-info`` exist to make pip's install
    importable and are dead weight here.
    """
    prefix = f"{name}/{name}-{version}/"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        members = [m for m in archive.namelist() if m.startswith(prefix)]
        if not members:
            raise RuntimeError(
                f"{wheel.name} does not contain the expected {prefix} directory"
            )
        for member in members:
            relative = member[len(prefix):]
            if not relative:
                continue
            # Defend against a path escaping the destination. The archive comes
            # from Explosion's releases over TLS, but unpacking an archive
            # without this check is how a supply-chain problem becomes a
            # filesystem problem.
            out_path = (destination / relative).resolve()
            if not str(out_path).startswith(str(destination.resolve())):
                raise RuntimeError(f"Refusing to unpack outside the target: {member}")
            if member.endswith("/"):
                out_path.mkdir(parents=True, exist_ok=True)
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _validate(directory: Path, name: str, version: str) -> None:
    """Prove the unpacked directory is a model spaCy can actually open.

    Structure first (cheap, and it names the missing file), then a real
    ``spacy.load``. The load is what separates "the bytes arrived" from "the
    model works", and it is worth the few seconds once per install: the
    alternative is discovering it at the moment someone anonymises a document.
    """
    for required in ("config.cfg", "meta.json"):
        if not (directory / required).is_file():
            raise RuntimeError(f"Unpacked {name} is missing {required}")
    meta = _read_meta(directory)
    if not meta:
        raise RuntimeError(f"Unpacked {name} has an unreadable meta.json")
    if meta.get("version") != version:
        raise RuntimeError(
            f"Unpacked {name} reports version {meta.get('version')!r}, "
            f"expected {version!r}"
        )
    import spacy

    spacy.load(directory)


def install(
    name: str,
    version: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> tuple[bool, str]:
    """Download and install ``name``, atomically replacing any previous copy.

    Everything happens in a temporary sibling directory and the installed model
    is only touched by the final move, so a network error, a truncated
    download or a model spaCy refuses to open all leave the previous version in
    place and working.
    """
    resolved = version or latest_compatible(name)
    if resolved is None:
        return False, (
            f"Could not resolve a version of {name} compatible with the "
            "installed spaCy (no network, or the model is not published)."
        )

    destination = model_dir(name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"{name}_download_", dir=str(destination.parent)
        ) as staging_name:
            staging = Path(staging_name)
            wheel = staging / "model.whl"
            if progress_callback:
                progress_callback(f"Downloading {name}", 0.05)
            _download_wheel(_wheel_url(name, resolved), wheel, progress_callback, name)

            unpacked = staging / "model"
            if progress_callback:
                progress_callback(f"Unpacking {name}", 0.80)
            _unpack_wheel(wheel, name, resolved, unpacked)

            if progress_callback:
                progress_callback(f"Checking {name}", 0.90)
            _validate(unpacked, name, resolved)

            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(unpacked), str(destination))
    except Exception as exc:  # noqa: BLE001 — reported, never raised at startup
        logger.warning("Install of %s failed: %s", name, exc)
        return False, f"Could not install {name}: {exc}"

    if progress_callback:
        progress_callback(f"Installed {name}", 1.0)
    logger.info("NER model installed: %s %s in %s", name, resolved, destination)
    return True, f"{name} {resolved} installed"


def status() -> list[dict]:
    """Per-model state for /api/health and the diagnostics bundle.

    Deliberately offline: it reports what is on disk. The "is there something
    newer" question costs a network round trip and is answered by the startup
    check, which writes its answer into this same picture via
    :func:`server.inference.ner_status`.
    """
    entries = []
    for name in MODEL_NAMES:
        managed = is_installed(name)
        entries.append({
            "name": name,
            "present": is_present(name),
            "source": "managed" if managed else ("pip" if is_pip_installed(name) else None),
            "version": installed_version(name),
            "path": str(model_dir(name)) if managed else None,
        })
    return entries
