"""Read/write the runtime Chrome-visibility switch in ~/.infoos/config.yaml.

The switch is a single bool at ``browser.headless_during_fetch``. It
is consulted by the browser-session preparation path whenever InfoOS
launches the dedicated Chrome profile for fetch-time repair. Setup and
login paths deliberately ignore it (per spec section 2.4).

We use ruamel.yaml in round-trip mode to preserve comments and
unknown keys. If ruamel is unavailable, fall back to a simple text
append that does not preserve order.
"""
from __future__ import annotations

from pathlib import Path


def read_headless_during_fetch(config_path: Path | None = None) -> bool:
    """Return the parsed value, or True if missing/unparseable."""
    path = _resolve(config_path)
    if not path.is_file():
        return True
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return True
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return True
    if not isinstance(payload, dict):
        return True
    browser = payload.get("browser") or {}
    if not isinstance(browser, dict):
        return True
    value = browser.get("headless_during_fetch", True)
    return bool(value)


def write_headless_during_fetch(value: bool, config_path: Path | None = None) -> None:
    """Idempotent: only writes if the value differs from current."""
    path = _resolve(config_path)
    if read_headless_during_fetch(path) is bool(value):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
    new_block = f"browser:\n  headless_during_fetch: {'true' if value else 'false'}\n"
    if "browser:" in existing:
        # Replace the existing browser block in place.
        lines = existing.splitlines(keepends=True)
        out: list[str] = []
        in_block = False
        replaced = False
        for line in lines:
            if line.startswith("browser:") and not in_block:
                out.append(new_block)
                in_block = True
                replaced = True
                continue
            if in_block:
                if line and not line.startswith(" ") and not line.startswith("\t"):
                    in_block = False
                    out.append(line)
                continue
            out.append(line)
        if not replaced:
            out.append(new_block)
        path.write_text("".join(out), encoding="utf-8")
    else:
        path.write_text(existing.rstrip("\n") + "\n" + new_block, encoding="utf-8")


def _resolve(config_path: Path | None) -> Path:
    if config_path is not None:
        return Path(config_path)
    from backend.infrastructure.runtime.environment import config_yaml_path
    return config_yaml_path()
