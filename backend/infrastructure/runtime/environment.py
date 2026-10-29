from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit


def _path_from_env(name: str, fallback: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    if raw:
        return Path(raw).expanduser()
    return fallback


def state_home() -> Path:
    return _path_from_env("INFOOS_STATE_HOME", Path.home() / ".infoos")


def chrome_profile_dir() -> Path:
    return _path_from_env("INFOOS_CHROME_PROFILE_DIR", state_home() / "chrome-profile")


def browser_session_json_path() -> Path:
    return _path_from_env("INFOOS_BROWSER_SESSION_JSON", state_home() / "browser-session.json")


def browser_session_lock_path() -> Path:
    return _path_from_env("INFOOS_BROWSER_SESSION_LOCK", state_home() / "browser-session.json.lock")


def config_yaml_path() -> Path:
    return _path_from_env("INFOOS_CONFIG_PATH", state_home() / "config.yaml")


def config_dir() -> Path:
    return _path_from_env("INFOOS_CONFIG_DIR", state_home() / "config")


def notifications_config_path() -> Path:
    return _path_from_env("INFOOS_NOTIFICATIONS_CONFIG", config_dir() / "notifications.json")


def setup_state_path() -> Path:
    return state_home() / "setup-state.json"


def setup_complete_flag_path() -> Path:
    return state_home() / "setup-complete.flag"


def in_flight_lock_path() -> Path:
    return _path_from_env("INFOOS_WATCHDOG_LOCK_PATH", state_home() / "in-flight.lock")


def opencli_browser_lock_path() -> Path:
    return _path_from_env("INFOOS_OPENCLI_LOCK_PATH", state_home() / "opencli-browser-session.lock")


def gpu_lock_path() -> Path:
    return _path_from_env("INFOOS_GPU_LOCK_PATH", state_home() / "gpu.lock")


def browser_console_url() -> str:
    """Return the same-origin Browser Console path exposed by the gateway."""
    raw = os.environ.get("INFOOS_BROWSER_CONSOLE_URL", "/browser-console/").strip()
    if not raw.startswith("/") or raw.startswith("//"):
        return "/browser-console/"
    return raw


def public_base_url() -> str | None:
    """Return a validated HTTPS origin used in externally opened links."""
    raw = os.environ.get("INFOOS_PUBLIC_BASE_URL", "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    authority = f"{host}:{port}" if port is not None else host
    return f"https://{authority}"


def faster_whisper_cuda_enabled() -> bool:
    return os.environ.get("INFOOS_FASTER_WHISPER_CUDA_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def faster_whisper_download_root() -> str | None:
    raw = os.environ.get("INFOOS_FASTER_WHISPER_DOWNLOAD_ROOT", "").strip()
    return str(Path(raw).expanduser()) if raw else None
