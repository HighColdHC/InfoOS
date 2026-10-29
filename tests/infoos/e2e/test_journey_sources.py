"""交互式来源维护、首次采集、审计和只读体检。"""
from types import SimpleNamespace

from backend.application.tasks.executor import run_tasks_by_ids
from backend.application.tasks.planner import plan_fetch_tasks_for_source
from backend.application.tasks.repository import TaskRepository
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import SourceRepository
from tests.infoos.e2e.support import PROFILE, START, assert_denied, call, drain, evaluation


def test_sources_journey(journey, tmp_path, monkeypatch):
    client, scheduled, interactive = journey
    request = {"platform": "rss", "source_type": "feed", "identifier": "https://example.test/feed.xml", "reason": "请订阅"}
    assert_denied(client, scheduled, "create_source", **request)
    created = call(client, interactive, "create_source", **request)
    sid = created["source_id"]
    assert created["initial_fetch"] and created["task_id"]
    initial = TaskRepository.for_base_dir(tmp_path).get(created["task_id"])
    assert initial.target_id == sid and initial.artifacts_summary["initial_fetch"]
    def connector(base, source):
        raw = RawItem(connector_id=source["connector_id"], raw_item_id="source-story", source_platform="rss",
                      source_type="article", source_url="https://example.test/story", captured_at=START,
                      raw_text="订阅来源的首次文章正文", raw_metadata={"title": "首次文章"})
        return SimpleNamespace(fetch_new_items=lambda **kw: FetchResult(items=[raw], next_cursor="next"))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", connector)
    result = run_tasks_by_ids(tmp_path, [created["task_id"]], max_workers=1)
    assert result["succeeded"] == 1, result
    drain(tmp_path)
    call(client, scheduled, "push_profile", markdown=PROFILE)
    card = call(client, scheduled, "list_new_cards", since=START)["items"][0]
    call(client, scheduled, "submit_evaluations", evaluator="journey", items=[evaluation(card["card_id"])])
    drain(tmp_path)
    duplicate = call(client, interactive, "create_source", **request)
    assert duplicate["error"] == "duplicate_source" and duplicate["existing_source_id"] == sid
    update = {"source_id": sid, "reason": "改名与降频", "changes": {"display_name": "我的 RSS", "cadence_hours": 24}}
    assert_denied(client, scheduled, "update_source", **update)
    call(client, interactive, "update_source", **update)
    disable = {"source_id": sid, "enabled": False, "reason": "先暂停"}
    assert_denied(client, scheduled, "set_source_enabled", **disable)
    call(client, interactive, "set_source_enabled", **disable)
    source = SourceRepository(tmp_path).get_source(sid)
    assert source["enabled"] is False and source["display_name"] == "我的 RSS"
    assert plan_fetch_tasks_for_source(tmp_path, source) == []
    audit = call(client, scheduled, "list_source_changes", source_id=sid)
    assert audit["total"] == 3
    rows = audit["items"]
    assert [r["action"] for r in rows] == ["disable", "update", "create"]
    assert [r["reason"] for r in rows] == ["先暂停", "改名与降频", "请订阅"]
    assert all(r["actor_kind"] == "mcp" and r["token_id"] == interactive["token"]["token_id"] for r in rows)
    assert rows[-1]["before_config_version"] is None
    assert all(rows[i]["before_config_version"] == rows[i+1]["after_config_version"] for i in range(2))
    before = call(client, scheduled, "list_sources")
    stats = call(client, scheduled, "get_source_stats", since=START, until="2099-01-01T00:00:00Z", min_evaluated=1)
    assert stats["totals"] == {"cards": 1, "evaluated": 1, "passed": 1, "pass_rate": 1.0}
    assert stats["items"][0]["source_id"] == sid and stats["items"][0]["verdict"] == "high_yield"
    assert stats["items"][0]["fetch_run_count"] == 1
    assert call(client, scheduled, "list_sources") == before
    assert call(client, scheduled, "list_source_changes", source_id=sid) == audit
    assert SourceRepository(tmp_path).get_source(sid) == source
