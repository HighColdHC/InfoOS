from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app

from backend.application.setup.state import AccountStatus
from backend.application.tasks.planner import plan_fetch_tasks_for_source, plan_initial_fetch
from backend.application.tasks.handlers.acquisition import _defer_outside_feed_window
from backend.application.tasks.types import Task, TaskType
from backend.application.workflows.acquisition import _ensure_personalized_feed_login
from backend.application.workflows.acquisition_failures import AcquisitionFailureError
from backend.application.workflows.fetch_support import build_connector
from backend.domain.sources.capability import resolve_source_capability
from backend.domain.sources.configuration import database_source_to_connector_config, validate_source_payload
from backend.infrastructure.storage.repositories import SourceRepository


def source(**changes):
    return validate_source_payload({
        "source_id": "dy_feed", "platform": "douyin", "source_type": "home_feed",
        "identifier": "recommended", "cadence_hours": 1, "enabled": True, **changes,
    })


def test_projection_defaults_and_connector_timeout(tmp_path):
    config = database_source_to_connector_config(source())
    assert config["type"] == "opencli"
    assert config["result_order"] == "snapshot"
    assert config["command"] == ["douyin", "feed", "--limit", "40", "--dwell_min", "6", "--dwell_max", "20", "--with_meta", "true"]
    assert config["timeout_seconds"] == build_connector(tmp_path, config).timeout_seconds == 2100
    assert config["field_mapping"] == {
        "id": "url", "title": "title", "url": "url", "text": "title",
        "author_name": "author", "author_handle": "author_handle", "author_url": "author_url", "published_at": "published_at",
    }
    custom = database_source_to_connector_config(source(max_items_per_run=50, raw_metadata={"dwell_min_seconds": 0, "dwell_max_seconds": 0}))
    assert custom["command"][-8:] == ["--limit", "50", "--dwell_min", "0", "--dwell_max", "0", "--with_meta", "true"]


def test_api_default_batch_and_metadata_limit_validation(tmp_path):
    client = TestClient(create_app(tmp_path))
    payload = {"platform": "douyin", "source_type": "home_feed", "identifier": "recommended",
               "display_name": "抖音推荐", "cadence_hours": 1, "source_id": "dy_api"}
    invalid = client.post("/api/configurable-sources", json={**payload, "max_items_per_run": 51})
    assert invalid.status_code == 422
    created = client.post("/api/configurable-sources", json=payload)
    assert created.status_code == 201
    stored = SourceRepository(tmp_path).get_source("dy_api")
    assert stored["raw_metadata"]["max_items_per_run"] == 40
    assert stored["raw_metadata"]["dwell_min_seconds"] == 6
    assert stored["raw_metadata"]["active_hours"] == {"start": "09:00", "end": "19:00"}


@pytest.mark.parametrize("kind", ["keyword", "creator", "item"])
def test_hourly_only_allowed_for_home_feed(kind):
    with pytest.raises(ValueError, match="cadence"):
        source(source_type=kind, identifier="https://www.douyin.com/video/9000000000000000007")


@pytest.mark.parametrize("changes", [
    {"identifier": "following"}, {"max_items_per_run": 51},
    {"raw_metadata": {"dwell_min_seconds": -1}}, {"raw_metadata": {"dwell_max_seconds": 61}},
    {"raw_metadata": {"dwell_min_seconds": 21}},
    {"raw_metadata": {"session_interval_min_minutes": 29}},
    {"raw_metadata": {"session_interval_max_minutes": 721}},
    {"raw_metadata": {"session_interval_min_minutes": 121}},
    {"raw_metadata": {"active_hours": {"start": "19:00", "end": "09:00"}}},
])
def test_invalid_source_options(changes):
    with pytest.raises(ValueError):
        source(**changes)


def test_snapshot_filters_lives_counts_skips_and_preserves_ad_metadata(tmp_path, monkeypatch):
    payload = [
        {"aweme_id": "1", "title": "video", "url": "https://www.douyin.com/video/1", "is_live": False,
         "author": "作者", "author_handle": "handle", "digg_count": 2, "share_count": 3,
         "is_ad": True, "ad_id": "12345678901234567890", "ad_kind": "aweme_dou_plus", "ad_is_soft": True,
         "play_urls": ["https://cdn.test/1", "https://cdn.test/2"]},
        {"is_live": True, "url": "", "title": "直播"},
        {"aweme_id": "2", "url": "", "title": "missing URL"},
    ]
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", run)
    connector = build_connector(tmp_path, database_source_to_connector_config(source()))
    first = connector.fetch_new_items(None)
    assert (first.visible_count, first.new_count, first.dedup_skipped_count) == (3, 2, 1)
    item = first.items[0]
    assert item.raw_author.handle == "handle"
    assert item.raw_metadata["ad_id"] == "12345678901234567890"
    assert item.raw_metadata["is_ad"] is True
    assert item.raw_metadata["ad_is_soft"] is True
    assert item.raw_metadata["shares"] == 3
    assert item.raw_metadata["play_urls"] == payload[0]["play_urls"]
    assert first.items[1].source_url == "https://www.douyin.com/video/2"
    second = connector.fetch_new_items(first.next_cursor)
    assert (second.visible_count, second.new_count, second.dedup_skipped_count) == (3, 0, 3)
    assert all(call["timeout"] == 2100 for call in calls)
    assert all(call["env"]["OPENCLI_BROWSER_COMMAND_TIMEOUT"] == "2100" for call in calls)


def test_connector_never_restarts_feed_on_detached_error(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run",
                        lambda *args, **kwargs: calls.append(args) or SimpleNamespace(
                            returncode=1, stdout="", stderr="Detached while handling command."))
    connector = build_connector(tmp_path, database_source_to_connector_config(source()))
    with pytest.raises(RuntimeError):
        connector.fetch_new_items(None)
    assert len(calls) == 1


def test_feed_requires_login_before_connector(tmp_path, monkeypatch):
    row = source()
    capability = resolve_source_capability(row)
    assert capability.requires_login == "required"
    monkeypatch.setattr("backend.application.setup.verify.probe_platform",
                        lambda platform: SimpleNamespace(status=AccountStatus.NEEDS_LOGIN, error=None))
    with pytest.raises(AcquisitionFailureError):
        _ensure_personalized_feed_login(row, database_source_to_connector_config(row), capability=capability)


def test_periodic_plans_one_session_and_initial_calibration_remains_immediate(tmp_path):
    row = {**source(), "connector_status": "supported"}
    outside = "2026-10-09T12:00:00Z"  # Shanghai 20:00
    assert plan_fetch_tasks_for_source(tmp_path, row, now=outside) == []
    tasks = plan_fetch_tasks_for_source(tmp_path, row, now=outside, horizon_hours=48)
    assert len(tasks) == 1
    assert tasks[0].next_run_at == "2026-10-10T01:00:00Z"
    assert plan_initial_fetch(row, now=outside).next_run_at == outside
    inside = plan_fetch_tasks_for_source(tmp_path, row, now="2026-10-09T02:00:00Z", horizon_hours=48)
    assert len(inside) == 1


def test_delayed_periodic_task_defers_outside_window_but_retry_does_not(tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source({**source(), "connector_status": "supported"})
    monkeypatch.setattr("backend.application.tasks.handlers.acquisition.utc_now_seconds_z", lambda: "2026-10-09T12:00:00Z")
    task = Task("periodic", TaskType.FETCH, "source", "dy_feed")
    deferred = _defer_outside_feed_window(task, tmp_path)
    assert deferred["status"] == "pending"
    assert deferred["next_run_at"] == "2026-10-10T01:00:00Z"
    assert _defer_outside_feed_window(replace(task, attempt=2), tmp_path) is None
    for key in ("initial_fetch", "manual_run"):
        assert _defer_outside_feed_window(replace(task, artifacts_summary={key: True}), tmp_path) is None
