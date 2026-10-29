"""Active component discovery for persisted collection-environment snapshots.

This is the only application use case allowed to probe local installation and
version state.  Read models must consume the persisted result instead.
"""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

import importlib.metadata
import sqlite3
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from backend.application.enrichment.asr.providers.sherpa import SherpaOnnxProvider
from backend.infrastructure.browser.setup_discovery import (
    find_chrome_binary,
    get_chrome_version,
    inspect_opencli_extension,
)
from backend.infrastructure.runtime.media_tools import media_tool_status
from backend.infrastructure.runtime.setup_state import (
    COMPONENT_IDS,
    ComponentState,
    load_state,
    save_state,
)


Probe = Callable[[], Mapping[str, Any]]


def refresh_component_snapshot(
    state_path: Path,
    *,
    probes: Mapping[str, Probe] | None = None,
    now: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Probe every component independently, then atomically persist one snapshot."""
    checked_at = (now or utc_now_micro_z)()
    selected_probes = dict(probes or _default_probes())
    components: dict[str, ComponentState] = {}
    for component_id in COMPONENT_IDS:
        probe = selected_probes.get(component_id)
        try:
            if probe is None:
                raise LookupError("probe missing")
            result = dict(probe())
            components[component_id] = ComponentState(
                status=str(result.get("status") or "unknown"),
                version=(str(result["version"]) if result.get("version") is not None else None),
                last_checked=checked_at,
                detail_code=(str(result["detail_code"]) if result.get("detail_code") else None),
            )
        except Exception:  # noqa: BLE001 - failures are persisted as stable facts
            components[component_id] = ComponentState(
                status="unknown",
                version=None,
                last_checked=checked_at,
                detail_code=f"{component_id}_probe_failed",
            )
    state = load_state(state_path)
    state.components = components
    state.component_snapshot_at = checked_at
    save_state(state_path, state)
    return {
        "snapshot_at": checked_at,
        "components": {
            component_id: {
                "status": component.status,
                "version": component.version,
                "last_checked": component.last_checked,
                "detail_code": component.detail_code,
            }
            for component_id, component in components.items()
        },
    }


def _default_probes() -> dict[str, Probe]:
    return {
        "dedicated_chrome": _probe_chrome,
        "opencli_bridge": _probe_opencli_bridge,
        "yt_dlp": lambda: _probe_media_tool("yt_dlp"),
        "gallery_dl": lambda: _probe_media_tool("gallery_dl"),
        "ffmpeg": lambda: _probe_media_tool("ffmpeg"),
        "local_asr": _probe_local_asr,
        "sqlite": _probe_sqlite,
    }


def _probe_chrome() -> dict[str, str | None]:
    binary = find_chrome_binary()
    if binary is None:
        return {"status": "missing", "version": None, "detail_code": "chrome_missing"}
    return {"status": "installed", "version": get_chrome_version(binary)}


def _probe_opencli_bridge() -> dict[str, str | None]:
    result = inspect_opencli_extension()
    if not result.get("loaded"):
        return {"status": "missing", "version": None, "detail_code": "opencli_bridge_missing"}
    version = result.get("version") or result.get("manifest_version")
    return {"status": "installed", "version": str(version) if version is not None else None}


def _probe_media_tool(tool_id: str) -> dict[str, str | None]:
    status = media_tool_status()[tool_id]
    path = status.get("path")
    if status.get("status") != "installed" or not path:
        return {"status": "missing", "version": None, "detail_code": f"{tool_id}_missing"}
    return {"status": "installed", "version": _command_version(str(path))}


def _command_version(executable: str) -> str | None:
    result = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        return None
    first_line = (result.stdout or result.stderr or "").strip().splitlines()
    return first_line[0][:160] if first_line else None


def _probe_local_asr() -> dict[str, str | None]:
    availability = SherpaOnnxProvider().is_available()
    if not availability.available:
        return {"status": "missing", "version": None, "detail_code": availability.reason}
    try:
        version = importlib.metadata.version("sherpa-onnx")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {"status": "installed", "version": version}


def _probe_sqlite() -> dict[str, str]:
    return {"status": "installed", "version": sqlite3.sqlite_version}
