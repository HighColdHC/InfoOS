from __future__ import annotations

import json

from backend.infrastructure.runtime.container_health import read_container_health


def test_container_health_is_unknown_without_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("INFOOS_CONTAINER_HEALTH_PATH", str(tmp_path / "missing.json"))
    assert read_container_health() == {
        "status": "unknown",
        "reasons": [],
        "checked_at": None,
    }


def test_container_health_projects_actionable_failure(tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({
        "status": "unhealthy",
        "reasons": ["opencli daemon unavailable"],
        "checked_at": "2026-07-22T12:00:00Z",
    }))
    monkeypatch.setenv("INFOOS_CONTAINER_HEALTH_PATH", str(path))

    assert read_container_health() == {
        "status": "unhealthy",
        "reasons": ["opencli daemon unavailable"],
        "checked_at": "2026-07-22T12:00:00Z",
    }
