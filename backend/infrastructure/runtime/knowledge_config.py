"""User-provided knowledge archive destinations; never reads PKM contents."""
from __future__ import annotations

import yaml

from backend.domain.intelligence.knowledge_kinds import KINDS
from backend.infrastructure.runtime.environment import config_yaml_path


def read_pkm_targets() -> dict[str, str]:
    path = config_yaml_path()
    if not path.is_file():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("knowledge configuration must be a mapping")
    knowledge = payload.get("knowledge", {})
    if not isinstance(knowledge, dict):
        raise ValueError("knowledge configuration must be a mapping")
    targets = knowledge.get("pkm_targets", {})
    if not isinstance(targets, dict) or any(
        key not in KINDS or not isinstance(value, str) for key, value in targets.items()
    ):
        raise ValueError("knowledge.pkm_targets must map known knowledge kinds to strings")
    return targets
