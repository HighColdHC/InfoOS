"""Notification file parsing and write-only secrets boundary."""
import json
import os
import tempfile
from copy import deepcopy
from backend.infrastructure.notifications import rules
from backend.infrastructure.notifications.lark_cli import configured

DEFAULTS = {
    "content_push": {"daily_brief_digest": True},
    "channels": {"feishu": {"enabled": False, "receive_id": ""}},
    "quiet_hours": {"start": "23:00", "end": "08:00", "timezone": "Asia/Shanghai"},
    "auth_qr": {"codes_per_round": 3, "round_interval_hours": 4, "max_rounds_per_day": 4},
    # The since floors mirror the Mac launchers: older backlog is never "pending".
    "agent_watchdog": {"evaluate_stall_hours": 30, "opportunities_stall_hours": 9,
                       "evaluate_since": "2026-09-16T00:00:00Z", "opportunities_since": "2026-10-14T00:00:00Z"},
}


def merge(target: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = value
    return target


def load_config() -> dict:
    defaults = merge(deepcopy(rules.DEFAULT_CONFIG), deepcopy(DEFAULTS))
    path = rules.DEFAULT_CONFIG_PATH
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid_notification_config")
        merge(defaults, value)
    defaults["channels"]["feishu"] = _feishu_config(defaults["channels"]["feishu"])
    return defaults


def _feishu_config(value: dict) -> dict:
    return {key: value[key] for key in DEFAULTS["channels"]["feishu"]}


def save_config(config: dict) -> None:
    config = deepcopy(config)
    config["channels"]["feishu"] = _feishu_config(config["channels"]["feishu"])
    path = rules.DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".notifications-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def readiness(config: dict) -> dict[str, bool]:
    feishu, email = config["channels"]["feishu"], config["email"]
    return {
        "feishu": config["enabled"] and feishu["enabled"] and feishu["receive_id"].startswith("ou_") and configured(),
        "email": config["enabled"] and all(email[k] for k in ("smtp_host", "smtp_port", "username", "password", "from_email", "to_email")),
    }


def public_config(config: dict) -> dict:
    result = deepcopy(config)
    result["email"]["password_configured"] = bool(result["email"].pop("password"))
    return result
