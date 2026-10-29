"""Discover Chrome binary and OpenCLI extension directory.

Chrome discovery: hard-coded macOS path first, then PATH, then the
common install location. Only Chrome is supported in v1 (per spec
section 1.2 non-goal: multi-browser).

Extension discovery: searches five locations, in priority order:

1. ``$INFOOS_OPENCLI_EXTENSION_DIR`` — the dev-mode load-unpacked
   directory the launcher script (``opencli-infoos-chrome``) passes
   via ``--load-extension=``. This is what the InfoOS runtime fetch
   path uses when available.

2. ``~/Downloads/opencli-extension/unpacked`` — the fixed local
   unpacked runtime used by the InfoOS launcher. This avoids stale
   per-state copies such as ``~/.infoos-test/opencli-extension-unpacked``.

3. ``<chrome-profile>/Default/Extensions/<id>/<version>/manifest.json``
   — the canonical Chrome Web Store location inside the dedicated
   InfoOS user-data-dir. The older direct ``Extensions`` layout is
   retained for unpacked/development profiles.

4. ``~/Library/Application Support/Google/Chrome/Default/Extensions/...``
   — the macOS Chrome webstore install location for the user's
   *daily* Chrome. The OpenCLI extension ID is
   ``ildkmabpimmkaediidaifkhjpohdnifk``. If the user has installed
   OpenCLI via the Chrome Web Store in their normal browser, the
   extension is on disk at this path.

5. The Chrome process command line — when Chrome is already running
   with ``--load-extension=<path>``, that path is the unpacked
   extension directory (dev mode). Pulled from the running browser
   process so we don't require the user to set an env var.

The first match wins. The lookup is by extension ``name`` field
(contains "opencli", case-insensitive).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from backend.infrastructure.browser.chrome_runtime import (
    MACOS_STABLE_CHROME,
    chrome_binary_candidates,
)
from backend.infrastructure.runtime.environment import chrome_profile_dir

DEFAULT_CHROME_BIN = MACOS_STABLE_CHROME
DEFAULT_CHROME_PROFILE_DIR = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default"
# InfoOS dedicated Chrome profile (different user-data-dir from the
# user's daily Chrome). The dedicated profile lives under ~/.infoos/
# per the program/data split.
DEFAULT_INFOOS_CHROME_PROFILE_DIR = chrome_profile_dir()
DEFAULT_DOWNLOADS_EXTENSION = Path.home() / "Downloads" / "opencli-extension" / "unpacked"

_OPENCLI_NAME_HINTS = ("opencli",)
_OPENCLI_WEBSTORE_ID = "ildkmabpimmkaediidaifkhjpohdnifk"

# macOS Chrome webstore install path. Hard-coded because the user's
# daily Chrome is the most likely install location when they install
# via the webstore in their default browser.
_MACOS_CHROME_WEBSTORE_ROOT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Google"
    / "Chrome"
    / "Default"
    / "Extensions"
)


def is_macos() -> bool:
    import platform

    return platform.system() == "Darwin"


def _chrome_search_paths() -> list[Path]:
    candidates = chrome_binary_candidates(system="Darwin" if is_macos() else None)
    if is_macos() and DEFAULT_CHROME_BIN not in candidates:
        candidates.append(DEFAULT_CHROME_BIN)
    return candidates


def find_chrome_binary() -> Path | None:
    for candidate in _chrome_search_paths():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def get_chrome_version(binary: Path) -> str | None:
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().rsplit(" ", 1)
    return parts[-1] if parts else None


def _is_opencli_manifest(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    name = str(payload.get("name", "")).lower()
    return any(hint in name for hint in _OPENCLI_NAME_HINTS)


def _scan_directory(root: Path) -> Path | None:
    """Look for a manifest.json containing 'opencli' in its name field.

    Webstore installs nest as
    ``Extensions/<ext-id>/<version>/manifest.json``; dev-mode unpacked
    installs are at the root of the directory the user passed to
    ``--load-extension=``. We try the manifest at every level so both
    layouts work.
    """
    if not root.is_dir():
        return None
    # Direct manifest at root (dev-mode unpacked layout).
    if _is_opencli_manifest(root / "manifest.json"):
        return root
    # Walk one level deeper for webstore layout.
    try:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if _is_opencli_manifest(child / "manifest.json"):
                return child
            # Web Store layout: root/<extension-id>/<version>/manifest.json.
            for grand in child.iterdir():
                if grand.is_dir() and _is_opencli_manifest(grand / "manifest.json"):
                    return grand
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return None


def _scan_running_chrome_load_extension() -> Path | None:
    """Inspect ``ps`` for any running Chrome with ``--load-extension=``.

    Returns the unpacked extension directory the running browser
    was launched with, or None if no Chrome is currently using
    ``--load-extension=`` (or we cannot enumerate processes).
    """
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    for line in result.stdout.splitlines():
        if "Google Chrome" not in line:
            continue
        match = re.search(r"--load-extension=([^\s]+)", line)
        if not match:
            continue
        candidate = Path(match.group(1).strip('"'))
        if _is_opencli_manifest(candidate / "manifest.json"):
            return candidate
    return None


def discover_extension_dir() -> Path | None:
    # 1. Explicit override.
    env_value = os.environ.get("INFOOS_OPENCLI_EXTENSION_DIR", "").strip()
    if env_value:
        candidate = Path(env_value).expanduser()
        if _is_opencli_manifest(candidate / "manifest.json"):
            return candidate
    # 2. Fixed local unpacked runtime used by scripts/opencli-infoos-chrome.
    if _is_opencli_manifest(DEFAULT_DOWNLOADS_EXTENSION / "manifest.json"):
        return DEFAULT_DOWNLOADS_EXTENSION
    # 3. The InfoOS dedicated Chrome user-data-dir. Web Store installs
    #    live below Default/Extensions; retain the older direct layout
    #    for profiles created by the unpacked-extension launcher.
    for infoos_root in (
        DEFAULT_INFOOS_CHROME_PROFILE_DIR / "Default" / "Extensions" / _OPENCLI_WEBSTORE_ID,
        DEFAULT_INFOOS_CHROME_PROFILE_DIR / "Extensions",
    ):
        found = _scan_directory(infoos_root)
        if found:
            return found
    # 4. The user's daily Chrome webstore install.
    if _MACOS_CHROME_WEBSTORE_ROOT.exists():
        try:
            for ext_id_dir in _MACOS_CHROME_WEBSTORE_ROOT.iterdir():
                if not ext_id_dir.is_dir():
                    continue
                if ext_id_dir.name != _OPENCLI_WEBSTORE_ID and _OPENCLI_WEBSTORE_ID not in ext_id_dir.name:
                    continue
                # Webstore layout: <ext-id>/<version>/manifest.json
                for version_dir in ext_id_dir.iterdir():
                    if version_dir.is_dir() and _is_opencli_manifest(version_dir / "manifest.json"):
                        return version_dir
        except (FileNotFoundError, PermissionError, OSError):
            pass
    # 5. Any currently-running Chrome with --load-extension=<...>.
    return _scan_running_chrome_load_extension()


def inspect_opencli_extension() -> dict[str, object | None]:
    """Return manifest metadata without applying setup workflow decisions."""
    ext_dir = discover_extension_dir()
    if ext_dir is None:
        return {"loaded": False, "id": None, "manifest_version": None, "error": "OpenCLI extension directory not found"}
    try:
        payload = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"loaded": False, "id": None, "manifest_version": None, "error": str(exc)}
    return {
        "loaded": True,
        "id": str(payload.get("name", "")).strip() or None,
        "manifest_version": int(payload.get("manifest_version", 0)) or None,
    }
