from __future__ import annotations

from backend.domain.timestamps import utc_now_offset

import json
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from backend.infrastructure.runtime.environment import config_dir, notifications_config_path

CONFIG_DIR = config_dir()
DEFAULT_CONFIG_PATH = notifications_config_path()

DEFAULT_CONFIG = {
    "enabled": False,
    "email": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "username": "",
        "password": "",
        "from_email": "",
        "to_email": "",
    },
    "rules": {
        "high_priority_min_score": 4,
        "high_priority_cooldown_minutes": 30,
        "daily_brief_time": "08:00",
        "source_failure_cooldown_hours": 6,
    },
}


def get_config() -> dict[str, Any]:
    """Load notification config, create default if missing."""
    if DEFAULT_CONFIG_PATH.exists():
        return json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFIG_PATH.write_text(
        json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return deepcopy(DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> None:
    """Persist a complete notification configuration."""
    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _rate_limit_dir(base_dir: Path) -> Path:
    d = base_dir / "data" / "notifications" / "rate_limits"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_due(rule_name: str, cooldown_minutes: int, base_dir: Path) -> bool:
    """Check if enough time has passed since last notification of this type."""
    limit_file = _rate_limit_dir(base_dir) / f"{rule_name}.json"
    if not limit_file.exists():
        return True
    try:
        data = json.loads(limit_file.read_text(encoding="utf-8"))
        last = datetime.fromisoformat(data["last_sent"])
        return (datetime.now(timezone.utc) - last) > timedelta(minutes=cooldown_minutes)
    except (json.JSONDecodeError, KeyError, ValueError):
        return True


def record_sent(rule_name: str, base_dir: Path) -> None:
    """Record that a notification was sent now."""
    limit_file = _rate_limit_dir(base_dir) / f"{rule_name}.json"
    limit_file.write_text(
        json.dumps({"last_sent": utc_now_offset()}, indent=2),
        encoding="utf-8",
    )


def is_daily_brief_time(config: dict[str, Any]) -> bool:
    """Check if it's time for the daily brief (within 1 hour of configured time)."""
    now = datetime.now(timezone.utc)
    target_hour = int(config["rules"]["daily_brief_time"].split(":")[0])
    return now.hour == target_hour
