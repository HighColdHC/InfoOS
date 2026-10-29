"""MCP collection contracts exercised through the authenticated transport."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.usecases.api_tokens import create_token
from backend.domain import agent_collect
from backend.domain.sources.registry import capability_catalog
from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository
from backend.infrastructure.storage.database import connect
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.repositories import CardRepository
from tests.infoos.test_mcp_interface import _call


REQUEST = {"name": "AI 调研", "keywords": ["AI"], "platforms": [
    {"platform": "x", "max_items": 5}, {"platform": "github", "max_items": 5},
]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("INFOOS_PUBLIC_BASE_URL", raising=False)
    # Public IP literals exercise the real safety policy without DNS/network.
    monkeypatch.setattr("backend.application.usecases.capture_policy.select_supported_proxy", lambda _: None)
    app = create_app(tmp_path)
    reader = create_token(tmp_path, name="reader", scopes=["intelligence:read"])
    writer = create_token(tmp_path, name="writer", scopes=["intelligence:read", "collect:write"])
    other = create_token(tmp_path, name="other", scopes=["collect:write"])
    with TestClient(app) as client:
        yield client, reader, writer, other


def invoke(client, token, tool_name, **arguments):
    result = _call(client, "tools/call", {"name": tool_name, "arguments": arguments}, token["plaintext_token"])
    assert result.status_code == 200
    return result.json()["result"]


def call(client, token, tool_name, **arguments):
    result = invoke(client, token, tool_name, **arguments)
    assert not result.get("isError"), result
    return result["structuredContent"]


def test_scopes_and_registry_capabilities(setup):
    client, reader, writer, other = setup
    for name, args in (("start_research", REQUEST), ("capture_url", {"url": "https://8.8.8.8/a"}),
                       ("get_capture", {"capture_id": "missing"})):
        denied = invoke(client, reader, name, **args)
        assert denied["isError"] is True
        assert "insufficient_scope" in denied["content"][0]["text"]
    capabilities = call(client, reader, "get_collect_capabilities")
    assert "quota" not in capabilities
    assert capabilities["research"] == [
        {"platform": item["platform"], "label": item["label"], **item["research"]}
        for item in capability_catalog() if item["research"] is not None
    ]
    assert not {"google", "wechat", "web"} & {item["platform"] for item in capabilities["research"]}
    assert capabilities["capture"]["capture_mode"] == "url_only"
    quota = call(client, writer, "get_collect_capabilities")["quota"]
    assert quota == {"window_hours": 24, "research": {"limit": 20, "remaining": 20},
                     "capture": {"limit": 200, "remaining": 200}}
    assert invoke(client, other, "get_collect_capabilities")["isError"] is True
    assert invoke(client, other, "get_research_run", research_run_id="missing")["isError"] is True
    assert call(client, reader, "get_research_run", research_run_id="missing")["error"] == "research_run_not_found"


def test_research_creation_idempotency_validation_and_roots(setup, tmp_path):
    client, reader, writer, _ = setup
    first = call(client, writer, "start_research", **REQUEST, idempotency_key="one")
    run_id = first["research_run_id"]
    assert first["replayed"] is False
    assert first["research_run"]["platform_count"] == 2
    assert "get_research_run" in first["hint"]
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        metadata = json.loads(conn.execute("select metadata from task_batches where batch_id = ?", (run_id,)).fetchone()[0])
        assert metadata["origin"] == {"kind": "mcp", "token_id": writer["token"]["token_id"]}
        roots = conn.execute("select t.task_type,s.platform,s.source_scope,t.artifacts_summary from tasks t join sources s on s.source_id=t.target_id join task_batch_items b on b.task_id=t.task_id where b.batch_id=?", (run_id,)).fetchall()
        assert {r["platform"] for r in roots} == {"x", "github"}
        assert all(r["task_type"] == "fetch" and r["source_scope"] == "research" for r in roots)
        assert all(json.loads(r["artifacts_summary"])["enforce_source_capability"] for r in roots)
    replay = call(client, writer, "start_research", **REQUEST, idempotency_key="one")
    assert replay["replayed"] and replay["research_run_id"] == run_id
    conflict = call(client, writer, "start_research", **{**REQUEST, "name": "Changed"}, idempotency_key="one")
    assert conflict["error"] == "idempotency_conflict" and conflict["research_run_id"] == run_id
    for platform in ("web", "google", "wechat", "made-up"):
        invalid = call(client, writer, "start_research", **{**REQUEST, "platforms": [{"platform": platform, "max_items": 1}]})
        assert invalid["error"] == "research_platform_unsupported"
    for changes in ({"platforms": [{"platform": "x", "max_items": 100000}]},
                    {"platforms": [{"platform": "x"}]}, {"date_range": {"unexpected": True}}):
        assert call(client, writer, "start_research", **{**REQUEST, **changes})["error"] == "invalid_request"
    detail = call(client, reader, "get_research_run", research_run_id=run_id)
    assert {r["platform"] for r in detail["platform_runs"]} == {"x", "github"}
    assert all(r["status"] == "queued" for r in detail["platform_runs"])
    assert detail["item_page"] == {"items": [], "next_cursor": None}
    assert call(client, reader, "get_research_run", research_run_id=run_id, cursor="bad")["error"] == "invalid_cursor"
    for limit in (0, 101):
        assert call(client, reader, "get_research_run", research_run_id=run_id, item_limit=limit)["error"] == "invalid_request"


def test_research_rolling_quota_replay_other_token_and_web(setup, tmp_path, monkeypatch):
    client, _, writer, other = setup
    monkeypatch.setattr(agent_collect, "MCP_RESEARCH_DAILY_LIMIT", 2)
    web = client.post("/api/research-runs", json=REQUEST, headers={"Idempotency-Key": "web"})
    assert web.status_code == 202
    one = call(client, writer, "start_research", **REQUEST, idempotency_key="one")
    assert "research_run_id" in call(client, writer, "start_research", **REQUEST, idempotency_key="two")
    rejected = call(client, writer, "start_research", **REQUEST, idempotency_key="three")
    assert rejected["error"] == "daily_limit_exceeded"
    assert (rejected["limit"], rejected["window_hours"]) == (2, 24)
    assert call(client, writer, "start_research", **REQUEST, idempotency_key="one")["replayed"]
    assert "research_run_id" in call(client, other, "start_research", **REQUEST, idempotency_key="other")
    assert call(client, writer, "get_collect_capabilities")["quota"]["research"]["remaining"] == 0
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from task_batches").fetchone()[0] == 4
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        conn.execute("update task_batches set created_at=? where batch_id=?", (old, one["research_run_id"]))
    assert call(client, writer, "get_collect_capabilities")["quota"]["research"]["remaining"] == 1
    assert "research_run_id" in call(client, writer, "start_research", **REQUEST, idempotency_key="three")


def test_research_card_page_links_and_fulltext_read(setup, tmp_path):
    client, reader, writer, _ = setup
    run_id = call(client, writer, "start_research", **REQUEST)["research_run_id"]
    for index in range(2):
        CardRepository(tmp_path).upsert_card({
            "card_id": f"card-{index}", "source_platform": "github", "source_type": "repository",
            "version": 1, "content_hash": "sha256:" + str(index) * 64,
            "source_url": f"https://example.com/{index}", "title": f"Card {index}", "content": "Body" * 3000,
            "status": "active", "completeness_status": "complete", "captured_at": "2026-10-14T00:00:00Z",
            "processed_at": "2026-10-14T00:00:00Z", "published_at": "2026-10-14T00:00:00Z",
        })
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        task_ids = [row[0] for row in conn.execute(
            "select task_id from task_batch_items where batch_id=? order by rowid", (run_id,)
        )]
        for index, task_id in enumerate(task_ids):
            conn.execute("update tasks set status='success',artifacts_summary=? where task_id=?",
                         (json.dumps({"card_id": f"card-{index}", "created_or_updated": "created"}), task_id))
    page = call(client, reader, "get_research_run", research_run_id=run_id, item_limit=1)
    assert page["research_run"]["completed_platform_count"] == 2
    item = page["item_page"]["items"][0]
    assert item["deep_link"] == {"url": f"/r/card/{item['card_id']}", "relative_url": True}
    assert "content" not in item and len(json.dumps(item)) < 1000
    next_page = call(client, reader, "get_research_run", research_run_id=run_id, item_limit=1,
                     cursor=page["item_page"]["next_cursor"])
    assert next_page["item_page"]["items"][0]["card_id"] != item["card_id"]
    assert next_page["item_page"]["next_cursor"] is None
    assert call(client, reader, "get_card", card_id=item["card_id"])["content"] == "Body" * 3000


def test_capture_durable_replay_quota_ownership_and_links(setup, tmp_path, monkeypatch):
    client, _, writer, other = setup
    monkeypatch.setattr(agent_collect, "MCP_CAPTURE_DAILY_LIMIT", 1)
    first = call(client, writer, "capture_url", url="https://8.8.8.8/a", client_capture_id="first")
    assert first["status"] == "accepted" and first["task_id"]
    stored = CaptureReceiptRepository(tmp_path).get(first["capture_id"])
    assert stored["capture_mode"] == "url_only" and stored["token_id"] == writer["token"]["token_id"]
    raw = RawItemRepository(tmp_path).get(first["raw_item_id"])
    assert raw["raw_payload"]["raw_metadata"]["client"] == {"name": "infoos-mcp", "version": "1"}
    assert raw["captured_at"].endswith("Z")
    replay = call(client, writer, "capture_url", url="https://8.8.8.8/a", client_capture_id="first")
    assert replay["status"] == "already_in_progress" and replay["capture_id"] == first["capture_id"]
    assert call(client, writer, "capture_url", url="https://8.8.8.8/b", client_capture_id="first")["error"] == "idempotency_conflict"
    rejected = call(client, writer, "capture_url", url="https://8.8.8.8/b")
    assert rejected["error"] == "daily_limit_exceeded" and rejected["limit"] == 1
    assert call(client, writer, "get_collect_capabilities")["quota"]["capture"]["remaining"] == 0
    assert call(client, writer, "get_capture", capture_id=first["capture_id"])["capture_id"] == first["capture_id"]
    assert call(client, other, "get_capture", capture_id=first["capture_id"])["error"] == "capture_not_found"
    assert "capture_id" in call(client, other, "capture_url", url="https://8.8.8.8/b")
    CardRepository(tmp_path).upsert_card({
        "card_id": "captured-card", "source_platform": "web", "source_type": "capture", "source_url": "https://8.8.8.8/a",
        "title": "Captured", "content": "Body", "status": "active", "completeness_status": "complete",
        "captured_at": "2026-10-14T00:00:00Z", "processed_at": "2026-10-14T00:00:00Z",
    })
    CaptureReceiptRepository(tmp_path).update_processing(
        first["capture_id"], processing_status="succeeded", card_id="captured-card", updated_at=stored["created_at"],
    )
    assert call(client, writer, "get_capture", capture_id=first["capture_id"])["deep_link"]["url"] == "/r/card/captured-card"
    assert call(client, writer, "capture_url", url="https://8.8.8.8/a", client_capture_id="first")["deep_link"]["url"] == "/r/card/captured-card"
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from capture_receipts").fetchone()[0] == 2
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        conn.execute("update capture_receipts set created_at=? where capture_id=?", (old, first["capture_id"]))
    assert call(client, writer, "get_collect_capabilities")["quota"]["capture"]["remaining"] == 1
    assert "capture_id" in call(client, writer, "capture_url", url="https://8.8.8.8/c")


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "ftp://8.8.8.8/", "file:///tmp/a", "http://[::1]/"])
def test_capture_rejects_unsafe_urls(setup, url):
    client, _, writer, _ = setup
    assert call(client, writer, "capture_url", url=url)["error"] == (
        "unsafe_capture_url" if url.startswith("http") else "invalid_capture_request")


def test_capture_validation_and_rate_limit(setup, monkeypatch):
    client, _, writer, _ = setup
    assert call(client, writer, "capture_url", url="https://8.8.8.8/", title="x" * 2049)["error"] == "title_too_large"
    assert call(client, writer, "capture_url", url="https://8.8.8.8/", client_capture_id="")["error"] == "invalid_capture_request"
    calls = []
    def reject(token_id, source):
        calls.append((token_id, source))
        return False
    monkeypatch.setattr("backend.api.mcp.collect_tools.admit_capture_request", reject)
    assert call(client, writer, "capture_url", url="https://8.8.8.8/")["error"] == "capture_rate_limited"
    assert calls == [(writer["token"]["token_id"], "mcp")]


def test_token_management_create_update_and_unknown_scope(setup):
    client, reader, _, _ = setup
    created = client.post("/api/token-auth/tokens", json={"name": "MCP", "scopes": ["collect:write"]})
    assert created.status_code == 201 and created.json()["token"]["scopes"] == ["collect:write"]
    path = f"/api/token-auth/tokens/{reader['token']['token_id']}"
    updated = client.patch(path, json={"scopes": ["intelligence:read", "collect:write"], "webhook_enabled": False})
    assert updated.status_code == 200 and "collect:write" in updated.json()["scopes"]
    assert "research_run_id" in call(client, reader, "start_research", **REQUEST)
    assert client.post("/api/token-auth/tokens", json={"name": "bad", "scopes": ["unknown:write"]}).status_code == 422
    assert client.patch(path, json={"scopes": ["unknown:write"], "webhook_enabled": False}).status_code == 422
    assert "collect:write" in client.get("/api/token-auth/overview").json()["capabilities"]
