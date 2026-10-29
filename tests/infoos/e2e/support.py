"""Shared journey mechanics: seed only acquisition inputs, execute real pipelines."""
from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
from backend.domain.models.raw_item import RawItem, RawTimestamps
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.repositories import CardRepository, SourceRepository
from tests.infoos._factories import make_source_payload
from tests.infoos.test_agent_evaluations import PROFILE, item as evaluation
from tests.infoos.test_mcp_collect import call, invoke

START = "2026-10-17T00:00:00Z"
ENTERED = "2026-10-17T12:00:00Z"


def drain(base, *types):
    """Drain each durable generation, including children planned by the real worker."""
    types = types or (TaskType.CARD_BUILD, TaskType.CONTENT_ENRICH, TaskType.MEDIA_DOWNLOAD, TaskType.CARD_FINALIZE)
    for _ in range(12):
        result = run_due_tasks(base, now="2099-01-01T00:00:00Z", limit=100,
                               max_workers=1, task_types=types)
        assert result["failed"] == 0, result
        assert all(r["status"] in {"success", "skipped"} for r in result["results"]), result
        if not result["processed"]:
            selected = [t for t in TaskRepository.for_base_dir(base).list_tasks() if t.task_type in types]
            assert all(t.status == TaskStatus.SUCCESS or
                       (t.task_type == TaskType.MEDIA_DOWNLOAD and t.status == TaskStatus.SKIPPED)
                       for t in selected), selected
            return
    raise AssertionError("任务链未在有界轮次内完成")


def seed_raw(base, monkeypatch, key, *, entered=ENTERED, platform="rss", source_type="feed", metadata=None):
    """Historical dates/prefilter evidence replace unavailable external acquisition only.

    No cards, evaluations or final task states are inserted: reconciliation plans
    CARD_BUILD and the actual worker builds/enriches/finalizes every card.
    """
    sid = f"journey-{platform}-{source_type}"
    sources = SourceRepository(base)
    if sources.get_source(sid) is None:
        sources.upsert_source(make_source_payload(
            source_id=sid, connector_id=sid, platform=platform, source_type=source_type,
            identifier="https://example.test/feed" if platform == "rss" else "all" if platform == "zsxq" else "1824528822",
            enabled=False,
        ))
    raw = RawItem(connector_id=sid, raw_item_id=key, source_platform=platform,
                  source_type="article" if platform == "rss" else source_type,
                  source_url=f"https://example.test/{key}", captured_at=entered,
                  raw_timestamps=RawTimestamps(published_at=entered),
                  raw_text=f"{key} 正文：培训课程的产品与成交证据。",
                  raw_metadata={"source_id": sid, "title": f"场景 {key}", **(metadata or {})})
    with monkeypatch.context() as clock:
        clock.setattr("backend.infrastructure.storage.raw_items.utc_now", lambda: entered)
        RawItemRepository(base).upsert_raw_items([raw])
    return raw


def build_seeded(base):
    result = reconcile_pipeline(base, limit=500)
    assert not result["errors"], result
    drain(base)
    with CardRepository(base).connect_existing() as conn:
        rows = conn.execute("select raw_item_id, card_id from cards where ignored = 0").fetchall()
    return {row["raw_item_id"]: row["card_id"] for row in rows}


def assert_denied(client, token, tool, **arguments):
    result = invoke(client, token, tool, **arguments)
    assert result["isError"] and "insufficient_scope" in result["content"][0]["text"], result


def reader_get(client, monkeypatch, path):
    # nginx calls this internal route before forwarding the actual reader request.
    # Fake only the external gateway's authenticated session lookup.
    monkeypatch.setattr("backend.infrastructure.runtime.webauth.gateway_session_authorized",
                        lambda cookie, **kw: cookie == "session=journey")
    auth = client.get("/api/internal/reader-authorize", headers={
        "X-InfoOS-Internal-Auth": "1", "X-Original-URI": path,
        "X-Original-Method": "GET", "X-InfoOS-Original-Cookie": "session=journey",
    })
    assert auth.status_code == 204
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response
