"""分享捕获、身份查重、滚动配额与注册表内按需调研。"""
from types import SimpleNamespace
from datetime import datetime, timedelta

from backend.application.tasks.executor import run_tasks_by_ids
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskType
from backend.domain import agent_collect
from backend.domain.models.raw_item import RawItem
from backend.domain.sources.registry import capability_catalog
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository
from backend.infrastructure.storage.database import connect
from backend.infrastructure.storage.paths import resolve_sqlite_path
from tests.infoos.e2e.support import START, assert_denied, call, drain
from tests.infoos.test_mcp_capture_dedup import public_network, redirect_chain  # noqa: F401


def counts(base):
    with connect(resolve_sqlite_path(base)) as conn:
        return tuple(conn.execute(f"select count(*) from {table}").fetchone()[0]
                     for table in ("capture_receipts", "tasks", "raw_items"))


def test_capture_journey(journey, public_network, tmp_path, monkeypatch):
    client, scheduled, interactive = journey
    monkeypatch.setattr(agent_collect, "MCP_CAPTURE_DAILY_LIMIT", 1)
    url = "https://www.douyin.com/video/1234567890"
    def fetch(connector, since_cursor):
        return FetchResult(items=[RawItem(
            connector_id=connector.connector_id, raw_item_id="1234567890", source_platform="douyin",
            source_type="video", source_url=url, captured_at=START, raw_text="培训课程的完整分享正文",
            raw_metadata={"title": "分享内容"},
        )], next_cursor=None)
    monkeypatch.setattr("backend.infrastructure.connectors.douyin.item.DouyinItemConnector.fetch_new_items", fetch)
    first = call(client, interactive, "capture_url", url=f"分享给你（{url}），值得看看。", client_capture_id="share-one")
    assert first["status"] == "accepted" and first["task_id"]
    progress = call(client, interactive, "get_capture", capture_id=first["capture_id"])
    assert progress["processing_status"] == "pending"
    replay = call(client, interactive, "capture_url", url=url, client_capture_id="share-one")
    assert replay["capture_id"] == first["capture_id"] and counts(tmp_path)[0] == 1
    assert replay["status"] == "already_in_progress"
    assert call(client, interactive, "capture_url", url="https://8.8.8.8/over-limit")["error"] == "daily_limit_exceeded"
    result = run_tasks_by_ids(tmp_path, [first["task_id"]], max_workers=1)
    assert result["succeeded"] == 1, result
    running = call(client, interactive, "get_capture", capture_id=first["capture_id"])
    assert running["processing_status"] == "running"
    drain(tmp_path)
    done = call(client, interactive, "get_capture", capture_id=first["capture_id"])
    assert done["processing_status"] == "succeeded" and done["card_id"]
    assert "完整分享正文" in call(client, scheduled, "get_card", card_id=done["card_id"])["content"]
    before = counts(tmp_path)
    quota = call(client, interactive, "get_collect_capabilities")["quota"]["capture"]
    assert quota == {"limit": 1, "remaining": 0}
    for alternate in (url + "?from=share", "https://www.iesdouyin.com/share/video/1234567890/"):
        found = call(client, interactive, "capture_url", url=alternate)
        assert found["status"] == "already_collected" and found["card_id"] == done["card_id"]
    redirect_chain(monkeypatch, [url])
    short = call(client, interactive, "capture_url", url="分享 https://v.douyin.com/journey/！")
    assert short["resolution"] == "resolved" and short["card_id"] == done["card_id"]
    assert counts(tmp_path) == before
    assert call(client, interactive, "get_collect_capabilities")["quota"]["capture"] == quota
    created = CaptureReceiptRepository(tmp_path).get(first["capture_id"])["created_at"]
    clock = datetime.fromisoformat(created.replace("Z", "+00:00"))
    # Advance the use case clock instead of rewriting receipt timestamps in SQL.
    with monkeypatch.context() as time:
        time.setattr("backend.application.usecases.manual_capture.utc_now", lambda: (
            clock + timedelta(hours=24, seconds=-1)).isoformat().replace("+00:00", "Z"))
        denied = call(client, interactive, "capture_url", url="https://8.8.8.8/window-next")
        assert denied["error"] == "daily_limit_exceeded" and denied["window_hours"] == 24
        time.setattr("backend.application.usecases.manual_capture.utc_now", lambda: (
            clock + timedelta(hours=24, seconds=1)).isoformat().replace("+00:00", "Z"))
        assert call(client, interactive, "capture_url", url="https://8.8.8.8/window-next")["status"] == "accepted"
    assert counts(tmp_path)[0] == 2
    for name, args in (("capture_url", {"url": url}), ("get_capture", {"capture_id": first["capture_id"]})):
        assert_denied(client, scheduled, name, **args)


def test_research_journey(journey, tmp_path, monkeypatch):
    client, scheduled, interactive = journey
    capabilities = call(client, scheduled, "get_collect_capabilities")
    assert capabilities["research"] == [
        {"platform": p["platform"], "label": p["label"], **p["research"]}
        for p in capability_catalog() if p["research"] is not None
    ]
    built = []
    def connector(base, source):
        built.append(source)
        items = [RawItem(connector_id=source["connector_id"], raw_item_id=f"research-{i}",
            source_platform="github", source_type="repository", source_url=f"https://github.com/test/journey-{i}",
            captured_at=START, raw_text=f"调研结果 {i} 的正文", raw_metadata={"title": f"调研 {i}"}) for i in range(2)]
        return SimpleNamespace(fetch_new_items=lambda **kw: FetchResult(items=items, next_cursor=None))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", connector)
    request = {"name": "用户上手调研", "keywords": ["InfoOS"], "platforms": [{"platform": "github", "max_items": 2}]}
    assert_denied(client, scheduled, "start_research", **request)
    unsupported = call(client, interactive, "start_research", **{**request, "platforms": [{"platform": "web", "max_items": 1}]})
    assert unsupported["error"] == "research_platform_unsupported"
    started = call(client, interactive, "start_research", **request)
    rid = started["research_run_id"]
    queued = call(client, scheduled, "get_research_run", research_run_id=rid)
    assert queued["platform_runs"][0]["status"] == "queued"
    assert call(client, scheduled, "get_research_run", research_run_id=rid, cursor="") == queued
    roots = [t for t in TaskRepository.for_base_dir(tmp_path).list_tasks() if t.task_type == TaskType.FETCH]
    assert len(roots) == 1
    result = run_tasks_by_ids(tmp_path, [t.task_id for t in roots], max_workers=1)
    assert result["succeeded"] == 1, result
    drain(tmp_path)
    assert len(built) == 1 and built[0]["research_platform"] == "github"
    first = call(client, scheduled, "get_research_run", research_run_id=rid, item_limit=1)
    run = first["platform_runs"][0]
    assert run["source_run_id"] == roots[0].task_id and run["status"] == "completed"
    # Management trace URLs deliberately use opaque aliases discovered by list.
    runs = client.get("/api/source-runs", params={"platform": "github", "task_view": "research"}).json()["items"]
    assert len(runs) == 1 and runs[0]["source_id"] == roots[0].target_id
    trace = client.get(f"/api/source-runs/{runs[0]['source_run_id']}/trace")
    assert trace.status_code == 200, trace.text
    assert trace.json()["item_page"]["item_total"] == 2
    assert first["research_run"]["completed_platform_count"] == 1
    second = call(client, scheduled, "get_research_run", research_run_id=rid, item_limit=1, cursor=first["item_page"]["next_cursor"])
    items = first["item_page"]["items"] + second["item_page"]["items"]
    assert len({i["card_id"] for i in items}) == 2 and second["item_page"]["next_cursor"] is None
    for item in items:
        detail = call(client, scheduled, "get_card", card_id=item["card_id"])
        assert detail["status"] == "active" and "调研结果" in detail["content"]
