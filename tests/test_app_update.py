"""Tests for the version comparison logic in app_update."""

import sys
from pathlib import Path

# Make the project root importable when running pytest from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_update import compare_versions


def test_equal_versions():
    assert compare_versions("1.3.2", "1.3.2") == 0


def test_current_older():
    assert compare_versions("1.3.1", "1.3.2") == -1
    assert compare_versions("1.2.9", "1.3.0") == -1
    assert compare_versions("0.9.0", "1.0.0") == -1


def test_current_newer():
    assert compare_versions("1.3.3", "1.3.2") == 1
    assert compare_versions("2.0.0", "1.9.9") == 1


def test_different_length_versions():
    # "1.3" should be treated as older than "1.3.0" (fewer components).
    assert compare_versions("1.3", "1.3.0") == -1
    assert compare_versions("1.3.0", "1.3") == 1


def test_malformed_versions_are_treated_as_equal():
    # Non-numeric input must not raise; it falls back to "equal" (0).
    assert compare_versions("abc", "1.2.3") == 0
    assert compare_versions("1.2.3", "") == 0


# --- Release asset selection -----------------------------------------------

import tarfile
import zipfile

import pytest

import app_update


# The asset names the release workflow actually publishes.
_RELEASE_ASSETS = [
    {"name": "PrivacyFilter-linux-v2.6.0.tar.gz",
     "browser_download_url": "https://example.invalid/linux.tar.gz"},
    {"name": "PrivacyFilter-macos-arm64-v2.6.0.tar.gz",
     "browser_download_url": "https://example.invalid/macos.tar.gz"},
    {"name": "PrivacyFilter-Setup-v2.6.0.exe",
     "browser_download_url": "https://example.invalid/setup.exe"},
]


def test_selects_the_macos_archive_on_apple_silicon(monkeypatch):
    monkeypatch.setattr(app_update.sys, "platform", "darwin")
    monkeypatch.setattr(app_update.platform, "machine", lambda: "arm64")
    assert app_update.select_asset(_RELEASE_ASSETS) == (
        "https://example.invalid/macos.tar.gz"
    )


def test_selects_the_linux_archive_on_linux(monkeypatch):
    monkeypatch.setattr(app_update.sys, "platform", "linux")
    assert app_update.select_asset(_RELEASE_ASSETS) == (
        "https://example.invalid/linux.tar.gz"
    )


def test_offers_nothing_on_windows_where_the_asset_is_an_installer(monkeypatch):
    """The .exe is an NSIS installer, not an archive to unpack over the files."""
    monkeypatch.setattr(app_update.sys, "platform", "win32")
    assert app_update.select_asset(_RELEASE_ASSETS) == ""


def test_offers_nothing_on_an_intel_mac_with_no_matching_build(monkeypatch):
    monkeypatch.setattr(app_update.sys, "platform", "darwin")
    monkeypatch.setattr(app_update.platform, "machine", lambda: "x86_64")
    assert app_update.select_asset(_RELEASE_ASSETS) == ""


def test_never_picks_another_platforms_archive(monkeypatch):
    """A release missing this platform's build must not install another's."""
    monkeypatch.setattr(app_update.sys, "platform", "darwin")
    monkeypatch.setattr(app_update.platform, "machine", lambda: "arm64")
    linux_only = [_RELEASE_ASSETS[0]]
    assert app_update.select_asset(linux_only) == ""


def test_asset_selection_survives_a_release_with_no_assets(monkeypatch):
    monkeypatch.setattr(app_update.sys, "platform", "linux")
    assert app_update.select_asset([]) == ""


# --- Archive handling ------------------------------------------------------


def test_archive_filename_keeps_the_suffix_the_extractor_reads():
    assert app_update._archive_filename(
        "https://example.invalid/d/PrivacyFilter-macos-arm64-v2.6.1.tar.gz"
    ) == "PrivacyFilter-macos-arm64-v2.6.1.tar.gz"
    # An unrecognisable URL still yields a name the tar branch can handle.
    assert app_update._archive_filename("https://example.invalid/download").endswith(
        ".tar.gz"
    )


def _stage_release(tmp_path, version: str):
    """Build a folder shaped like a release archive's single top-level dir."""
    stage = tmp_path / f"PrivacyFilter-macos-arm64-v{version}"
    (stage / "server").mkdir(parents=True)
    (stage / "VERSION").write_text(version, encoding="utf-8")
    (stage / "server" / "pdf_ops.py").write_text("# new code\n", encoding="utf-8")
    return stage


@pytest.mark.parametrize("kind", ["tar.gz", "zip"])
def test_extract_archive_unpacks_both_formats(tmp_path, kind):
    stage = _stage_release(tmp_path, "2.6.1")
    archive = tmp_path / f"release.{kind}"
    if kind == "tar.gz":
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(stage, arcname=stage.name)
    else:
        with zipfile.ZipFile(archive, "w") as zf:
            for item in stage.rglob("*"):
                zf.write(item, Path(stage.name) / item.relative_to(stage))

    out = tmp_path / "out"
    app_update._extract_archive(archive, out)
    assert (out / stage.name / "VERSION").read_text(encoding="utf-8") == "2.6.1"


def test_extract_archive_refuses_a_tar_member_escaping_the_destination(tmp_path):
    """The `data` filter must stop a path traversal, archive of ours or not."""
    evil = tmp_path / "evil.txt"
    evil.write_text("pwned", encoding="utf-8")
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(evil, arcname="../escaped.txt")

    with pytest.raises(tarfile.TarError):
        app_update._extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_download_and_install_a_tar_gz_release_over_an_install(tmp_path, monkeypatch):
    """End to end over `file://`: the macOS/Linux tarball is what ships.

    Before this, the asset scan only matched `.zip` and the extractor only read
    zip, so on a real release the archive path was unreachable and the fallback
    (`git pull`) could not work in an extracted tarball.
    """
    stage = _stage_release(tmp_path, "2.6.1")
    archive = tmp_path / "PrivacyFilter-macos-arm64-v2.6.1.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(stage, arcname=stage.name)

    install = tmp_path / "install"
    (install / "server").mkdir(parents=True)
    (install / "VERSION").write_text("2.6.0", encoding="utf-8")
    (install / "server" / "pdf_ops.py").write_text("# old code\n", encoding="utf-8")
    # User state living beside the code must survive the update untouched.
    (install / "model").mkdir()
    (install / "model" / "weights.bin").write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(app_update, "PROJECT_DIR", install)

    ok, message = app_update.download_and_install_update(archive.as_uri())

    assert ok, message
    assert (install / "server" / "pdf_ops.py").read_text(encoding="utf-8") == "# new code\n"
    assert (install / "VERSION").read_text(encoding="utf-8") == "2.6.1"
    assert (install / "model" / "weights.bin").read_text(encoding="utf-8") == "keep me"


# --- TLS verification ------------------------------------------------------


def test_ssl_context_verifies_against_a_bundled_ca_store():
    """The platform store cannot be relied on; certifi's must be used.

    A python.org framework Python on macOS ships no CA bundle until the user
    runs Install Certificates.command, so every GitHub call fails with
    CERTIFICATE_VERIFY_FAILED — and since the update check swallows network
    errors, the app silently reports no update forever.
    """
    import ssl

    context = app_update.ssl_context()

    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    # A bundle was actually loaded, rather than an empty default store.
    assert context.cert_store_stats()["x509_ca"] > 0


def test_ssl_context_falls_back_to_the_default_when_the_bundle_is_unusable(monkeypatch):
    """Returning None means "use urlopen's default" — never "skip verification"."""
    import certifi

    monkeypatch.setattr(certifi, "where", lambda: (_ for _ in ()).throw(OSError("gone")))
    assert app_update.ssl_context() is None
