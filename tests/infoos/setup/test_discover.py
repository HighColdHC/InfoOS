"""Discovery: find Chrome binary, version, and the unpacked extension dir."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.infrastructure.browser import setup_discovery as discover  # noqa: E402


def test_find_chrome_binary_returns_known_path_on_macos(monkeypatch, tmp_path):
    fake = tmp_path / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("INFOOS_CHROME_BIN", str(fake))
    monkeypatch.setattr(discover, "DEFAULT_CHROME_BIN", fake)
    monkeypatch.setattr(discover, "is_macos", lambda: True)
    found = discover.find_chrome_binary()
    assert found == fake


def test_find_chrome_binary_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(discover, "is_macos", lambda: True)
    monkeypatch.setattr(
        discover,
        "_chrome_search_paths",
        lambda: [Path("/nonexistent/Google Chrome")],
    )
    assert discover.find_chrome_binary() is None


def test_find_chrome_binary_prefers_linux_environment_override(monkeypatch, tmp_path):
    chrome = tmp_path / "google-chrome-stable"
    chrome.write_text("#!/bin/sh\n", encoding="utf-8")
    chrome.chmod(0o755)
    monkeypatch.setenv("INFOOS_CHROME_BIN", str(chrome))
    monkeypatch.setattr(discover, "is_macos", lambda: False)

    assert discover.find_chrome_binary() == chrome


def test_get_chrome_version_parses_version_string(monkeypatch):
    fake_bin = Path("/fake/chrome")
    monkeypatch.setattr(
        discover.subprocess,
        "run",
        lambda *args, **kwargs: type("R", (), {"stdout": "Google Chrome 148.0.7778.216\n", "returncode": 0})(),
    )
    version = discover.get_chrome_version(fake_bin)
    assert version == "148.0.7778.216"


def test_discover_extension_dir_prefers_env_var(monkeypatch, tmp_path):
    ext = tmp_path / "opencli-extension" / "unpacked"
    ext.mkdir(parents=True)
    (ext / "manifest.json").write_text('{"name": "OpenCLI Bridge"}', encoding="utf-8")
    monkeypatch.setenv("INFOOS_OPENCLI_EXTENSION_DIR", str(ext))
    assert discover.discover_extension_dir() == ext


def test_discover_extension_dir_prefers_fixed_downloads_extension(monkeypatch, tmp_path):
    downloads = tmp_path / "Downloads" / "opencli-extension" / "unpacked"
    downloads.mkdir(parents=True)
    (downloads / "manifest.json").write_text('{"name": "OpenCLI Bridge"}', encoding="utf-8")
    stale_profile = tmp_path / "Default" / "Extensions" / "abc123"
    stale_profile.mkdir(parents=True)
    (stale_profile / "manifest.json").write_text('{"name": "OpenCLI Browser Bridge"}', encoding="utf-8")

    monkeypatch.delenv("INFOOS_OPENCLI_EXTENSION_DIR", raising=False)
    monkeypatch.setattr(discover, "DEFAULT_DOWNLOADS_EXTENSION", downloads)
    monkeypatch.setattr(discover, "DEFAULT_INFOOS_CHROME_PROFILE_DIR", tmp_path / "Default")

    assert discover.discover_extension_dir() == downloads


def test_discover_extension_dir_falls_back_to_chrome_profile_scan(monkeypatch, tmp_path):
    profile_dir = tmp_path / "Default" / "Extensions" / "abc123"
    profile_dir.mkdir(parents=True)
    (profile_dir / "manifest.json").write_text('{"name": "OpenCLI Browser Bridge"}', encoding="utf-8")
    monkeypatch.delenv("INFOOS_OPENCLI_EXTENSION_DIR", raising=False)
    monkeypatch.setattr(discover, "DEFAULT_DOWNLOADS_EXTENSION", tmp_path / "missing-downloads")
    monkeypatch.setattr(discover, "DEFAULT_INFOOS_CHROME_PROFILE_DIR", tmp_path / "Default")
    found = discover.discover_extension_dir()
    assert found == profile_dir


def test_discover_extension_dir_finds_webstore_extension_in_dedicated_default_profile(monkeypatch, tmp_path):
    """Linux Web Store installs live below <user-data-dir>/Default/Extensions."""
    version_dir = (
        tmp_path
        / "chrome-profile"
        / "Default"
        / "Extensions"
        / discover._OPENCLI_WEBSTORE_ID
        / "1.0.22_0"
    )
    version_dir.mkdir(parents=True)
    (version_dir / "manifest.json").write_text(
        '{"name": "OpenCLI Browser Bridge", "manifest_version": 3}',
        encoding="utf-8",
    )
    monkeypatch.delenv("INFOOS_OPENCLI_EXTENSION_DIR", raising=False)
    monkeypatch.setattr(discover, "DEFAULT_DOWNLOADS_EXTENSION", tmp_path / "missing-downloads")
    monkeypatch.setattr(discover, "DEFAULT_INFOOS_CHROME_PROFILE_DIR", tmp_path / "chrome-profile")
    monkeypatch.setattr(discover, "_MACOS_CHROME_WEBSTORE_ROOT", tmp_path / "no-webstore")
    monkeypatch.setattr(discover, "_scan_running_chrome_load_extension", lambda: None)

    assert discover.discover_extension_dir() == version_dir


def test_dedicated_webstore_scan_rejects_opencli_named_decoy_id(monkeypatch, tmp_path):
    decoy = (
        tmp_path
        / "chrome-profile"
        / "Default"
        / "Extensions"
        / "not-the-opencli-webstore-id"
        / "9.9.9"
    )
    decoy.mkdir(parents=True)
    (decoy / "manifest.json").write_text(
        '{"name": "OpenCLI Browser Bridge", "manifest_version": 3}',
        encoding="utf-8",
    )
    monkeypatch.delenv("INFOOS_OPENCLI_EXTENSION_DIR", raising=False)
    monkeypatch.setattr(discover, "DEFAULT_DOWNLOADS_EXTENSION", tmp_path / "missing-downloads")
    monkeypatch.setattr(discover, "DEFAULT_INFOOS_CHROME_PROFILE_DIR", tmp_path / "chrome-profile")
    monkeypatch.setattr(discover, "_MACOS_CHROME_WEBSTORE_ROOT", tmp_path / "no-webstore")
    monkeypatch.setattr(discover, "_scan_running_chrome_load_extension", lambda: None)

    assert discover.discover_extension_dir() is None


def test_discover_extension_dir_returns_none_when_not_found(monkeypatch, tmp_path):
    monkeypatch.delenv("INFOOS_OPENCLI_EXTENSION_DIR", raising=False)
    monkeypatch.setattr(discover, "DEFAULT_CHROME_PROFILE_DIR", tmp_path / "missing")
    monkeypatch.setattr(discover, "DEFAULT_DOWNLOADS_EXTENSION", tmp_path / "missing-downloads")
    monkeypatch.setattr(discover, "DEFAULT_INFOOS_CHROME_PROFILE_DIR", tmp_path / "missing-infoos-profile")
    # Disable the additional lookups added for webstore / running-Chrome
    # detection so this test isolates the "no extension anywhere" path.
    monkeypatch.setattr(discover, "_MACOS_CHROME_WEBSTORE_ROOT", tmp_path / "no-webstore")
    monkeypatch.setattr(discover, "_scan_running_chrome_load_extension", lambda: None)
    assert discover.discover_extension_dir() is None


def test_discover_extension_dir_finds_webstore_install(monkeypatch, tmp_path):
    monkeypatch.delenv("INFOOS_OPENCLI_EXTENSION_DIR", raising=False)
    monkeypatch.setattr(discover, "DEFAULT_CHROME_PROFILE_DIR", tmp_path / "missing")
    monkeypatch.setattr(discover, "DEFAULT_DOWNLOADS_EXTENSION", tmp_path / "missing-downloads")
    monkeypatch.setattr(discover, "DEFAULT_INFOOS_CHROME_PROFILE_DIR", tmp_path / "missing-infoos-profile")
    monkeypatch.setattr(discover, "_scan_running_chrome_load_extension", lambda: None)
    webstore_root = tmp_path / "Chrome" / "Default" / "Extensions"
    version_dir = webstore_root / discover._OPENCLI_WEBSTORE_ID / "1.2.3"
    version_dir.mkdir(parents=True)
    (version_dir / "manifest.json").write_text('{"name": "OpenCLI Bridge"}', encoding="utf-8")
    monkeypatch.setattr(discover, "_MACOS_CHROME_WEBSTORE_ROOT", webstore_root)
    found = discover.discover_extension_dir()
    assert found == version_dir


def test_discover_extension_dir_finds_running_chrome_load_extension(monkeypatch, tmp_path):
    monkeypatch.delenv("INFOOS_OPENCLI_EXTENSION_DIR", raising=False)
    monkeypatch.setattr(discover, "DEFAULT_CHROME_PROFILE_DIR", tmp_path / "missing")
    monkeypatch.setattr(discover, "DEFAULT_DOWNLOADS_EXTENSION", tmp_path / "missing-downloads")
    monkeypatch.setattr(discover, "DEFAULT_INFOOS_CHROME_PROFILE_DIR", tmp_path / "missing-infoos-profile")
    monkeypatch.setattr(discover, "_MACOS_CHROME_WEBSTORE_ROOT", tmp_path / "no-webstore")
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    (unpacked / "manifest.json").write_text('{"name": "OpenCLI Dev"}', encoding="utf-8")
    monkeypatch.setattr(
        discover,
        "_scan_running_chrome_load_extension",
        lambda: unpacked,
    )
    found = discover.discover_extension_dir()
    assert found == unpacked
