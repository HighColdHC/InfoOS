"""Web app route tests — source detail, cadence, configurable sources, runs, domains."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.infoos._factories import card_count
from backend.infrastructure.browser import session_state
from backend.infrastructure.browser.session_state import BrowserSessionState
from backend.app.main import create_app
from backend.infrastructure.storage.repositories import CardRepository, RunRepository, RawItemRepository, SourceRepository
from backend.application.tasks.repository import TaskRepository
from backend.domain.models.raw_item import RawItem


def test_platform_capability_api_matches_source_configuration_contract(tmp_path):
    response = TestClient(create_app(tmp_path)).get("/api/platform-capabilities")

    assert response.status_code == 200
    catalog = {entry["platform"]: entry for entry in response.json()["platforms"]}
    assert [entry["source_type"] for entry in catalog["x"]["source_types"]] == ["creator", "list", "home_feed"]
    assert catalog["x"]["requires_browser_session"] is True
    assert catalog["x"]["default_max_items"] == 50
    assert catalog["x"]["max_items_limit"] == 100
    assert catalog["rss"]["requires_browser_session"] is False
    douyin_item = next(item for item in catalog["douyin"]["source_types"] if item["source_type"] == "item")
    assert douyin_item["label"] == "单个作品"
    assert douyin_item["default_cadence_hours"] == 24
    assert douyin_item["default_max_items"] == 1
    assert "短链" in douyin_item["identifier_hint"]
    zsxq_keyword = next(
        item for item in catalog["zsxq"]["source_types"] if item["source_type"] == "keyword"
    )
    assert zsxq_keyword["max_items_limit"] == 100
    assert "scys" not in catalog

def test_unrun_configurable_source_delete_cleans_pending_tasks_and_versions(tmp_path):
    client = TestClient(create_app(tmp_path))
    payload = {
        "source_id": "rss_ai",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "display_name": "AI Feed",
        "default_domain_id": None,
        "cadence_hours": 1,
        "notes": "original",
        "enabled": True,
    }

    created = client.post("/api/configurable-sources", json=payload)
    assert created.status_code == 201

    updated = client.put("/api/configurable-sources/rss_ai", json={**payload, "display_name": "AI Feed Updated", "cadence_hours": 5})
    assert updated.status_code == 200
    assert updated.json()["source"]["display_name"] == "AI Feed Updated"

    listed = client.get("/api/configurable-sources")
    assert listed.json()["sources"][0]["display_name"] == "AI Feed Updated"
    with SourceRepository(tmp_path).connect() as conn:
        assert conn.execute("select count(*) from source_config_versions where source_id = ?", ("rss_ai",)).fetchone()[0] >= 1
    assert TaskRepository.for_base_dir(tmp_path).list_by_target(target_kind="source", target_id="rss_ai")

    deleted = client.delete("/api/configurable-sources/rss_ai")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "source_id": "rss_ai"}
    assert SourceRepository(tmp_path).get_source("rss_ai") is None
    assert TaskRepository.for_base_dir(tmp_path).list_by_target(target_kind="source", target_id="rss_ai") == []
    with SourceRepository(tmp_path).connect() as conn:
        assert conn.execute("select count(*) from source_config_versions where source_id = ?", ("rss_ai",)).fetchone()[0] == 0

    disabled = client.patch(
        "/api/configurable-sources/rss_ai/enabled",
        json={"enabled": False},
    )
    assert disabled.status_code == 404


def test_configurable_source_with_run_history_cannot_be_deleted(tmp_path):
    client = TestClient(create_app(tmp_path))
    payload = {
        "source_id": "rss_history", "platform": "rss", "source_type": "feed",
        "identifier": "https://example.com/history.xml", "display_name": "History Feed",
        "cadence_hours": 1, "enabled": True,
    }
    assert client.post("/api/configurable-sources", json=payload).status_code == 201
    RunRepository(tmp_path).append_run({
        "run_id": "run_history", "source_id": "rss_history", "connector_id": "rss_history",
        "scheduled_for": "2026-09-23T00:00:00Z", "started_at": "2026-09-23T00:00:01Z",
        "finished_at": "2026-09-23T00:00:02Z", "status": "success",
        "fetched_count": 0, "new_count": 0, "skipped_count": 0, "error_count": 0,
        "duration_ms": 1000, "next_run_at": None, "attempt": 1,
        "trigger": "manual", "decision": None, "error_summary": None, "raw_payload": {},
    })
    RawItemRepository(tmp_path).upsert_raw_items([RawItem(
        raw_item_id="rss_history_item", connector_id="rss_history",
        source_platform="rss", source_type="feed",
        source_url="https://example.com/history/1",
        captured_at="2026-09-23T00:00:02Z", raw_text="Historical body",
        raw_metadata={"source_id": "rss_history"},
    )], run_id="run_history")
    CardRepository(tmp_path).upsert_card(_managed_card("rss_history"))

    deleted = client.delete("/api/configurable-sources/rss_history")

    assert deleted.status_code == 409
    assert deleted.json()["code"] == "source_has_history"
    assert SourceRepository(tmp_path).get_source("rss_history") is not None


def test_configurable_source_create_enqueues_versioned_initial_fetch(tmp_path, monkeypatch):
    from backend.application.tasks.repository import TaskRepository
    from backend.domain.sources.configuration import database_source_to_connector_config

    monkeypatch.setattr(
        "backend.api.routes.sources._now_utc",
        lambda: "2026-06-23T00:00:00Z",
        raising=False,
    )
    client = TestClient(create_app(tmp_path))
    payload = {
        "source_id": "rss_hourly",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "display_name": "Hourly Feed",
        "default_domain_id": None,
        "cadence_hours": 24,
        "notes": "future tasks",
        "enabled": True,
        "max_items_per_run": 3,
    }

    created = client.post("/api/configurable-sources", json=payload)

    assert created.status_code == 201
    body = created.json()
    assert body["queue_status"] == "calibration_pending"
    assert body["calibration_task_id"]
    assert body["initial_fetch_task_id"] == body["calibration_task_id"]
    assert body["initial_fetch_queue_status"] == "pending"
    assert body["task_ids"] == [body["calibration_task_id"]]
    tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(target_kind="source", target_id="rss_hourly")
    assert [task.task_type.value for task in tasks] == ["fetch"]
    assert tasks[0].artifacts_summary["initial_fetch"] is True
    listed = client.get("/api/configurable-sources").json()["sources"]
    assert [source["source_id"] for source in listed] == ["rss_hourly"]
    assert listed[0]["max_items_per_run"] == 3
    connector = database_source_to_connector_config(listed[0])
    assert connector["feed_url"] == "https://example.com/feed.xml"


def test_cosmetic_source_edit_preserves_supported_status_and_calibration_version(tmp_path):
    client = TestClient(create_app(tmp_path))
    payload = {
        "source_id": "rss_cosmetic",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/cosmetic.xml",
        "display_name": "Original name",
        "cadence_hours": 24,
        "notes": "original note",
        "enabled": True,
        "max_items_per_run": 3,
    }
    created = client.post("/api/configurable-sources", json=payload).json()
    SourceRepository(tmp_path).record_calibration_result(
        calibration={
            "calibration_id": "cal_cosmetic",
            "source_id": "rss_cosmetic",
            "platform": "rss",
            "source_type": "feed",
            "identifier": payload["identifier"],
            "result": "success",
            "task_id": created["calibration_task_id"],
            "visible_count": 1,
            "sample_count": 1,
        },
        connector_status="supported",
    )

    updated = client.put(
        "/api/configurable-sources/rss_cosmetic",
        json={**payload, "display_name": "Renamed", "notes": "cosmetic only"},
    )

    assert updated.status_code == 200
    assert updated.json()["source"]["connector_status"] == "supported"
    assert updated.json()["calibration_task_id"] is None
    assert updated.json()["queue_status"] == "ready"


def test_configurable_source_create_rejects_unregistered_source_type(tmp_path):
    client = TestClient(create_app(tmp_path))

    created = client.post(
        "/api/configurable-sources",
        json={
            "source_id": "wechat_ai",
            "platform": "wechat",
            "source_type": "keyword",
            "identifier": "AI",
            "display_name": "WeChat AI",
            "cadence_hours": 24,
            "enabled": True,
        },
    )

    assert created.status_code == 422
    assert created.json()["code"] == "invalid_source_configuration"
    assert SourceRepository(tmp_path).get_source("wechat_ai") is None
    assert TaskRepository.for_base_dir(tmp_path).list_tasks() == []


def test_x_source_create_exposes_max_items_per_run_to_connector(tmp_path):
    from backend.domain.sources.configuration import database_source_to_connector_config

    client = TestClient(create_app(tmp_path))
    created = client.post(
        "/api/configurable-sources",
        json={
            "source_id": "x_elon",
            "platform": "x",
            "source_type": "creator",
            "identifier": "elonmusk",
            "display_name": "Elon Musk",
            "cadence_hours": 24,
            "enabled": True,
            "max_items_per_run": 3,
        },
    )

    assert created.status_code == 201
    listed = client.get("/api/configurable-sources").json()["sources"]
    assert listed[0]["max_items_per_run"] == 3
    connector = database_source_to_connector_config(listed[0])
    assert connector["command"] == ["twitter", "tweets", "elonmusk", "--limit", "3"]


def test_configurable_sources_ignore_invalid_registry_when_database_has_sources(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "source-registry.yaml").write_text(
        "sources:\n"
        "  - source_id: bluesky_disabled\n"
        "    platform: bluesky\n"
        "    source_kind: creator\n"
        "    identity: alice.example\n"
        "    connector_id: bluesky_disabled\n"
        "    cadence: daily\n"
        "    enabled: false\n"
        "    source_lane: candidate\n"
        "    topics: [ai]\n"
        "    purpose: disabled future source\n"
        "    review_policy: paused\n",
        encoding="utf-8",
    )
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "AI Feed",
            "default_domain_id": None,
            "cadence_hours": 1,
            "notes": "AI feed",
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_ai",
        }
    )
    client = TestClient(create_app(tmp_path))

    listed = client.get("/api/configurable-sources")

    assert listed.status_code == 200
    assert [source["source_id"] for source in listed.json()["sources"]] == ["rss_ai"]


def test_configurable_source_detail_api_includes_latest_run_without_legacy_projections(tmp_path):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "AI Feed",
            "default_domain_id": None,
            "cadence_hours": 1,
            "notes": "AI feed",
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_ai",
        }
    )
    RunRepository(tmp_path).append_run(
        {
            "run_id": "run_rss_ai",
            "source_id": "rss_ai",
            "connector_id": "rss_ai",
            "scheduled_for": "2026-05-14T01:00:00Z",
            "started_at": "2026-05-14T01:00:00Z",
            "finished_at": "2026-05-14T01:00:02Z",
            "status": "success",
            "fetched_count": 3,
            "new_count": 2,
            "skipped_count": 1,
            "error_count": 0,
            "duration_ms": 2000,
            "next_run_at": "2026-05-14T02:00:02Z",
            "attempt": 1,
            "trigger": "scheduled",
            "raw_payload": {},
        }
    )
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/configurable-sources/rss_ai")

    assert response.status_code == 200
    assert response.json()["source"]["source_id"] == "rss_ai"
    assert response.json()["source"]["latest_run_at"] == "2026-05-14T01:00:02Z"
    assert not {"runs", "readiness", "calibration", "content_quality"} & response.json().keys()


SOURCE_ITEM_FIELDS = {
    "source_id", "display_name", "platform", "source_type", "identifier",
    "health_status", "health_label", "status_explanation", "cadence_hours",
    "max_items_per_run", "auto_enrich_media", "enabled", "latest_run_at",
    "latest_run_status", "readiness_status", "connector_id", "notes",
    "next_run_at", "generated_card_count", "current_config_version",
    "config_effective_at", "login_status", "raw_metadata",
}


def _managed_source(source_id: str, *, enabled: bool = True, display_name: str | None = None):
    return {
        "source_id": source_id,
        "platform": "rss",
        "source_type": "feed",
        "identifier": f"https://example.test/{source_id}.xml",
        "display_name": display_name or source_id,
        "cadence_hours": 24,
        "notes": f"notes for {source_id}",
        "enabled": enabled,
        "connector_status": "supported",
        "connector_id": f"connector-{source_id}",
        "raw_metadata": {"max_items_per_run": 12, "auto_enrich_media": False},
    }


def _managed_run(source_id: str, status: str, *, hour: int):
    recent_day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    return {
        "run_id": f"run-{source_id}",
        "source_id": source_id,
        "connector_id": f"connector-{source_id}",
        "scheduled_for": f"{recent_day}T{hour:02d}:00:00Z",
        "started_at": f"{recent_day}T{hour:02d}:00:01Z",
        "finished_at": f"{recent_day}T{hour:02d}:00:02Z",
        "status": status,
        "fetched_count": 2 if status == "success" else 0,
        "new_count": 1 if status == "success" else 0,
        "error_count": 0 if status == "success" else 1,
        "duration_ms": 1000,
        "attempt": 1,
        "trigger": "scheduled",
        "error_summary": None if status == "success" else "persisted fetch failed",
        "raw_payload": {},
    }


def _managed_card(source_id: str):
    return {
        "card_id": f"card-{source_id}",
        "card_type": "information",
        "version": 1,
        "content_hash": "sha256:" + "a" * 64,
        "updated_at": "2026-07-16T12:00:00Z",
        "status": "active",
        "completeness_status": "complete",
        "source_id": source_id,
        "source_platform": "rss",
        "source_type": "feed",
        "source_url": f"https://example.test/items/{source_id}",
        "source_author": "Example",
        "title": f"Card for {source_id}",
        "content": "body",
        "captured_at": "2026-07-16T12:00:00Z",
        "processed_at": "2026-07-16T12:00:00Z",
    }


def test_source_management_page_uses_persisted_health_metrics_and_server_filters(tmp_path):
    source_repo = SourceRepository(tmp_path)
    for source in (
        _managed_source("healthy-source", display_name="Healthy Feed"),
        _managed_source("attention-source", display_name="Attention Feed"),
        _managed_source("disabled-source", enabled=False, display_name="Disabled Feed"),
    ):
        source_repo.save_configuration(source, change_reason="created")
    RunRepository(tmp_path).append_run(_managed_run("healthy-source", "success", hour=12))
    RunRepository(tmp_path).append_run(_managed_run("attention-source", "failed", hour=11))
    CardRepository(tmp_path).upsert_card(_managed_card("healthy-source"))
    client = TestClient(create_app(tmp_path))

    response = client.get(
        "/api/configurable-sources",
        params={"query": "Healthy", "platform": "rss", "status": "healthy", "limit": 1, "offset": 0},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {
        "total_sources": 3,
        "enabled_sources": 2,
        "needs_attention": 1,
        "reliability_7d": 0.5,
    }
    assert payload["total"] == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert set(item) == SOURCE_ITEM_FIELDS
    assert item["source_id"] == "healthy-source"
    assert item["health_status"] == "healthy"
    assert item["health_label"] == "健康"
    assert item["max_items_per_run"] == 12
    assert item["auto_enrich_media"] is False
    assert item["latest_run_status"] == "success"
    assert item["generated_card_count"] == 1
    assert item["current_config_version"].startswith("sha256:")

    second_page = client.get(
        "/api/configurable-sources",
        params={"platform": "rss", "limit": 1, "offset": 1},
    ).json()
    assert second_page["total"] == 3
    assert len(second_page["items"]) == 1
    attention = client.get(
        "/api/configurable-sources",
        params={"status": "needs_attention"},
    ).json()
    assert [item["source_id"] for item in attention["items"]] == ["attention-source"]
    disabled = client.get(
        "/api/configurable-sources",
        params={"status": "disabled"},
    ).json()
    assert [item["source_id"] for item in disabled["items"]] == ["disabled-source"]


def test_source_management_page_matches_detail_across_filters_and_pages(tmp_path):
    from backend.read_models.source_cadence import suggest_source_cadences
    from backend.read_models.source_management import (
        _matches_query,
        read_source_detail,
        _summary,
        read_source_page,
    )

    source_repo = SourceRepository(tmp_path)
    for source in (
        _managed_source("alpha", display_name="Alpha Feed"),
        _managed_source("beta", display_name="Beta Feed"),
        _managed_source("gamma", enabled=False, display_name="Gamma Feed"),
        {**_managed_source("delta", display_name="Delta Feed"), "connector_status": "partial"},
        {
            **_managed_source("x-source", display_name="X Feed"),
            "platform": "x", "source_type": "creator", "identifier": "openai",
        },
    ):
        source_repo.save_configuration(source, change_reason="created")
    RunRepository(tmp_path).append_run(_managed_run("alpha", "success", hour=12))
    RunRepository(tmp_path).append_run(_managed_run("beta", "failed", hour=11))
    RunRepository(tmp_path).append_run(_managed_run("delta", "success", hour=11))
    CardRepository(tmp_path).upsert_card(_managed_card("alpha"))

    sources = source_repo.list_sources()
    details = [read_source_detail(tmp_path, source["source_id"]) for source in sources]
    detail_items = [detail["source"] for detail in details]
    detail_summary = _summary(
        detail_items,
        {detail["source"]["source_id"]: detail["recent_run_stats"] for detail in details},
    )
    sources_by_id = {str(source["source_id"]): source for source in sources}
    cases = (
        {"limit": 1, "offset": 0},
        {"limit": 1, "offset": 3},
        {"limit": 2, "offset": 4},
        {"query": "FEED", "platform": "rss", "status": "healthy", "limit": 1},
        {"query": "beta", "platform": "rss", "status": "needs_attention"},
        {"platform": "x", "status": "needs_attention"},
        {"status": "disabled"},
    )
    for case in cases:
        filtered = list(detail_items)
        if case.get("query"):
            filtered = [item for item in filtered if _matches_query(item, case["query"].strip().casefold())]
        if case.get("platform"):
            filtered = [item for item in filtered if item["platform"] == case["platform"]]
        if case.get("status"):
            filtered = [item for item in filtered if item["health_status"] == case["status"]]
        filtered.sort(key=lambda item: (item["display_name"].casefold(), item["source_id"]))
        limit, offset = case.get("limit", 25), case.get("offset", 0)
        expected_items = filtered[offset : offset + limit]
        expected = {
            "summary": detail_summary,
            "items": expected_items,
            "total": len(filtered),
            "limit": limit,
            "offset": offset,
            "cadence_suggestions": suggest_source_cadences(
                tmp_path, [sources_by_id[item["source_id"]] for item in expected_items],
            ),
        }
        assert read_source_page(tmp_path, **case) == expected


def test_source_management_page_reads_persisted_login_without_cookie_probe(tmp_path, monkeypatch):
    from backend.read_models.source_management import read_source_page

    state_home = tmp_path / "state"
    state_home.mkdir()
    (state_home / "setup-state.json").write_text(
        json.dumps({"accounts": {"x": {"status": "verified"}}}), encoding="utf-8",
    )
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))
    monkeypatch.setattr("backend.read_models.source_management_page.session_state.read", lambda: object())
    monkeypatch.setattr("backend.read_models.source_management_page.session_state.is_alive", lambda _state: True)
    monkeypatch.setattr(
        "backend.infrastructure.browser.cookie_status.check_cookie_login",
        lambda *_args: pytest.fail("list must not inspect Chrome cookies"),
    )
    source = {
        **_managed_source("x-persisted"),
        "platform": "x", "source_type": "creator", "identifier": "openai",
    }
    SourceRepository(tmp_path).save_configuration(source, change_reason="created")
    RunRepository(tmp_path).append_run(_managed_run("x-persisted", "success", hour=12))

    assert read_source_page(tmp_path)["items"][0]["health_status"] == "healthy"


def test_source_management_detail_returns_version_and_persisted_run_stats(tmp_path, monkeypatch):
    from backend.read_models import source_management_page

    for name in ("load_source_card_facts", "load_source_run_facts"):
        original = getattr(source_management_page, name)

        def scoped(conn, source_ids=None, *, original=original):
            assert source_ids == ["detail-source"]
            return original(conn, source_ids)

        monkeypatch.setattr(source_management_page, name, scoped)
    monkeypatch.setattr(
        CardRepository, "list_cards",
        lambda *_args, **_kwargs: pytest.fail("detail must not hydrate all cards"),
        raising=False,
    )
    SourceRepository(tmp_path).save_configuration(
        _managed_source("detail-source", display_name="Detail Feed"),
        change_reason="created",
    )
    managed_run = _managed_run("detail-source", "success", hour=10)
    old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    RunRepository(tmp_path).append_run({
        **managed_run, "run_id": "old-detail-run", "status": "failed",
        "finished_at": old_time, "started_at": old_time, "scheduled_for": old_time,
    })
    RunRepository(tmp_path).append_run(managed_run)
    CardRepository(tmp_path).upsert_card(_managed_card("detail-source"))

    response = TestClient(create_app(tmp_path)).get("/api/configurable-sources/detail-source")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload["source"]) == SOURCE_ITEM_FIELDS
    assert payload["source"]["source_id"] == "detail-source"
    assert payload["status_explanation"] == "最近一次采集成功。"
    assert payload["current_config_version"].startswith("sha256:")
    assert payload["config_effective_at"]
    assert payload["recent_run_stats"] == {
        "total": 1,
        "success": 1,
        "failed": 0,
        "cards_created": 1,
        "last_run_at": managed_run["finished_at"],
        "last_status": "success",
    }


def test_source_management_returns_stable_read_errors(tmp_path):
    client = TestClient(create_app(tmp_path))

    missing = client.get("/api/configurable-sources/missing")
    invalid_status = client.get("/api/configurable-sources", params={"status": "unknown"})
    invalid_limit = client.get("/api/configurable-sources", params={"limit": 0})

    assert missing.status_code == 404
    assert missing.json() == {"code": "source_not_found", "message": "Source not found"}
    assert invalid_status.status_code == 422
    assert invalid_status.json() == {
        "code": "invalid_source_status",
        "message": "status must be healthy, needs_attention or disabled",
    }
    assert invalid_limit.status_code == 422
    assert invalid_limit.json() == {
        "code": "invalid_pagination",
        "message": "limit must be an integer between 1 and 100",
    }


@pytest.mark.parametrize("account_status", ["needs_login", "failed"])
def test_source_management_projects_persisted_browser_login_blocker_passively(
    tmp_path, monkeypatch, account_status
):
    state_home = tmp_path / "state"
    state_home.mkdir()
    (state_home / "setup-state.json").write_text(
        json.dumps({"accounts": {"x": {"status": account_status}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))
    source = _managed_source("browser-source", display_name="Browser Source")
    source.update({"platform": "x", "source_type": "creator", "identifier": "openai"})
    SourceRepository(tmp_path).save_configuration(source, change_reason="created")
    RunRepository(tmp_path).append_run(_managed_run("browser-source", "success", hour=12))

    payload = TestClient(create_app(tmp_path)).get("/api/configurable-sources").json()
    item = payload["items"][0]

    assert item["login_status"] == account_status
    assert item["readiness_status"] == "login_required"
    assert item["health_status"] == "needs_attention"
    assert item["health_label"] == "需处理"
    assert "采集环境" in item["status_explanation"]
    assert payload["summary"]["needs_attention"] == 1


def test_source_management_zsxq_unverified_login_remains_actionable_after_success(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "backend.infrastructure.browser.session_state.read", lambda: object()
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.session_state.is_alive", lambda _state: True
    )
    state_home = tmp_path / "state"
    state_home.mkdir()
    (state_home / "setup-state.json").write_text(
        json.dumps({"accounts": {"zsxq": {"status": "needs_login"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))
    source = _managed_source("zsxq-source", display_name="ZSXQ Source")
    source.update({"platform": "zsxq", "source_type": "keyword", "identifier": "AI"})
    SourceRepository(tmp_path).save_configuration(source, change_reason="created")
    RunRepository(tmp_path).append_run(_managed_run("zsxq-source", "success", hour=12))

    item = TestClient(create_app(tmp_path)).get("/api/configurable-sources").json()["items"][0]

    assert item["login_status"] == "needs_login"
    assert item["readiness_status"] == "login_required"
    assert item["health_status"] == "needs_attention"
    assert "登录" in item["status_explanation"]


@pytest.mark.parametrize(
    ("source_type", "identifier", "expected_readiness"),
    [
        ("channel", "@openai", "ready"),
        ("keyword", "agentic AI", "ready"),
        ("home_feed", "recommended", "login_required"),
    ],
)
def test_source_management_applies_youtube_login_per_source_type(
    tmp_path, monkeypatch, source_type, identifier, expected_readiness,
):
    monkeypatch.setattr(
        "backend.infrastructure.browser.session_state.read", lambda: object()
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.session_state.is_alive", lambda _state: True
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.cookie_status.check_cookie_login",
        lambda _platform, _profile_dir: {"status": "logged_out"},
    )
    state_home = tmp_path / "state"
    state_home.mkdir()
    (state_home / "setup-state.json").write_text(
        json.dumps({"accounts": {"youtube": {"status": "needs_login"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))
    source = _managed_source(
        f"youtube-{source_type}",
        display_name=f"YouTube {source_type}",
    )
    source.update(
        {
            "platform": "youtube",
            "source_type": source_type,
            "identifier": identifier,
        }
    )
    SourceRepository(tmp_path).save_configuration(source, change_reason="created")

    item = TestClient(create_app(tmp_path)).get(
        "/api/configurable-sources"
    ).json()["items"][0]

    assert item["login_status"] == "needs_login"
    assert item["readiness_status"] == expected_readiness


@pytest.mark.parametrize("account_status", ["verified", "unknown"])
def test_source_management_does_not_misclassify_verified_or_unknown_account(
    tmp_path, monkeypatch, account_status
):
    monkeypatch.setattr(
        "backend.infrastructure.browser.session_state.read", lambda: object()
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.session_state.is_alive", lambda _state: True
    )
    state_home = tmp_path / "state"
    state_home.mkdir()
    (state_home / "setup-state.json").write_text(
        json.dumps({"accounts": {"x": {"status": account_status}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))
    source = _managed_source("browser-source", display_name="Browser Source")
    source.update({"platform": "x", "source_type": "creator", "identifier": "openai"})
    SourceRepository(tmp_path).save_configuration(source, change_reason="created")
    RunRepository(tmp_path).append_run(_managed_run("browser-source", "success", hour=12))

    payload = TestClient(create_app(tmp_path)).get("/api/configurable-sources").json()
    item = payload["items"][0]

    assert item["login_status"] == account_status
    assert item["readiness_status"] != "login_required"
    assert item["health_status"] == "healthy"
    assert item["health_label"] == "健康"
    assert payload["summary"]["needs_attention"] == 0


def test_source_management_keeps_disabled_status_ahead_of_login_blocker(tmp_path, monkeypatch):
    state_home = tmp_path / "state"
    state_home.mkdir()
    (state_home / "setup-state.json").write_text(
        json.dumps({"accounts": {"x": {"status": "needs_login"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INFOOS_STATE_HOME", str(state_home))
    source = _managed_source(
        "disabled-browser-source", enabled=False, display_name="Disabled Browser Source",
    )
    source.update({"platform": "x", "source_type": "creator", "identifier": "openai"})
    SourceRepository(tmp_path).save_configuration(source, change_reason="created")
    RunRepository(tmp_path).append_run(
        _managed_run("disabled-browser-source", "success", hour=12),
    )

    payload = TestClient(create_app(tmp_path)).get("/api/configurable-sources").json()
    item = payload["items"][0]

    assert item["login_status"] == "needs_login"
    assert item["readiness_status"] == "login_required"
    assert item["health_status"] == "disabled"
    assert item["health_label"] == "已停用"
    assert item["status_explanation"] == "来源已停用，不再参与定时调度。"
    assert payload["summary"]["needs_attention"] == 0


def _target_source_payload(**overrides):
    payload = {
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.test/managed-target.xml",
        "display_name": "Managed target",
        "cadence_hours": 24,
        "max_items_per_run": 10,
        "enabled": True,
        "notes": "target payload",
        "raw_metadata": {"auto_enrich_media": True},
    }
    return payload | overrides


def test_source_management_writes_return_real_ids_and_stable_errors(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    monkeypatch.setattr(
        "backend.api.routes.sources.read_source_detail",
        lambda *_args: pytest.fail("lifecycle must not compute source detail"),
    )
    monkeypatch.setattr(
        "backend.read_models.source_management.suggest_source_cadences",
        lambda *_args: pytest.fail("lifecycle must not compute cadence suggestions"),
    )

    created = client.post("/api/configurable-sources", json=_target_source_payload())

    assert created.status_code == 201
    body = created.json()
    assert "ok" not in body
    assert body["source_id"] == body["source"]["source_id"]
    assert body["source_run_id"] is None
    assert body["task_id"]
    assert body["initial_fetch"] is True
    assert body["reused"] is False
    source_id = body["source_id"]

    duplicate = client.post(
        "/api/configurable-sources",
        json=_target_source_payload(display_name="Duplicate"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "code": "duplicate_source",
        "message": "Source already exists",
        "existing_source_id": source_id,
    }

    display_update = client.put(
        f"/api/configurable-sources/{source_id}",
        json=_target_source_payload(display_name="Renamed target"),
    )
    assert display_update.status_code == 200
    assert display_update.json()["source"]["display_name"] == "Renamed target"
    assert display_update.json()["task_id"] is None

    fetch_update = client.put(
        f"/api/configurable-sources/{source_id}",
        json=_target_source_payload(display_name="Renamed target", max_items_per_run=20),
    )
    assert fetch_update.status_code == 200
    assert fetch_update.json()["initial_fetch"] is True
    assert fetch_update.json()["task_id"] != body["task_id"]

    immutable = client.put(
        f"/api/configurable-sources/{source_id}",
        json=_target_source_payload(identifier="https://example.test/changed.xml"),
    )
    assert immutable.status_code == 422
    assert immutable.json() == {
        "code": "immutable_source_identity",
        "message": "platform, source_type and identifier cannot be changed",
        "fields": ["identifier"],
    }


def test_source_management_activation_preserves_persisted_history(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    monkeypatch.setattr(
        "backend.api.routes.sources.read_source_detail",
        lambda *_args: pytest.fail("lifecycle must not compute source detail"),
    )
    monkeypatch.setattr(
        "backend.read_models.source_management.suggest_source_cadences",
        lambda *_args: pytest.fail("lifecycle must not compute cadence suggestions"),
    )
    created = client.post("/api/configurable-sources", json=_target_source_payload()).json()
    source_id = created["source_id"]
    RunRepository(tmp_path).append_run(_managed_run(source_id, "success", hour=9))
    CardRepository(tmp_path).upsert_card(_managed_card(source_id))
    task_ids_before = [
        task.task_id
        for task in TaskRepository.for_base_dir(tmp_path).list_by_target(
            target_kind="source", target_id=source_id
        )
    ]

    response = client.patch(
        f"/api/configurable-sources/{source_id}/enabled",
        json={"enabled": False},
    )

    assert response.status_code == 200
    assert response.json()["source"]["enabled"] is False
    assert response.json()["task_id"] is None
    assert len(RunRepository(tmp_path).list_runs(source_id=source_id)) == 1
    assert card_count(tmp_path) == 1
    assert [
        task.task_id
        for task in TaskRepository.for_base_dir(tmp_path).list_by_target(
            target_kind="source", target_id=source_id
        )
    ] == task_ids_before
