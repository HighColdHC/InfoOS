from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.usecases.api_tokens import create_token
from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository


def _call(client: TestClient, method: str, params: dict | None = None, token: str | None = None):
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        "/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def test_mcp_auth_tools_audit_and_same_origin_exemption(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFOOS_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("INFOOS_PUBLIC_BASE_URL", raising=False)
    app = create_app(tmp_path)
    CardRepository(tmp_path).upsert_card({
        "card_id": "card-one", "card_type": "information", "version": 1,
        "content_hash": "sha256:" + "a" * 64, "status": "active",
        "completeness_status": "partial", "missing_reasons": ["image_missing"],
        "source_platform": "rss", "source_type": "article",
        "source_url": "https://example.test/one", "source_author": "Author",
        "title": "Research note", "content": "Original body",
        "published_at": "2026-07-16T00:00:00Z", "updated_at": "2026-07-16T01:00:00Z",
        "captured_at": "2026-07-16T00:10:00Z", "processed_at": "2026-07-16T01:00:00Z",
    })
    CardContentBlockRepository(tmp_path).upsert_block({
        "block_id": "block-one", "card_id": "card-one", "position": 1,
        "layer": "original", "block_kind": "card_content", "status": "preserved",
        "body": "Research body from block",
    })
    for position, kind, body in ((2, "intel_summary", "评估摘要"), (3, "relation_note", "与你的关系")):
        CardContentBlockRepository(tmp_path).upsert_block({
            "block_id": f"evaluation-{kind}", "card_id": "card-one", "position": position,
            "layer": "intelligence", "block_kind": kind, "status": "enriched", "body": body,
            "provenance": "intelligence.evaluate", "raw_payload": {"purpose": "intelligence.evaluate"},
        })
    asset_path = tmp_path / "data" / "assets" / "card-one" / "image.jpg"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(b"image")
    CardAssetRepository(tmp_path).upsert_asset({
        "asset_id": "asset-one", "card_id": "card-one", "asset_kind": "image",
        "source_url": "https://example.test/image.jpg", "local_path": "card-one/image.jpg",
        "status": "downloaded", "mime_type": "image/jpeg",
    })
    reader = create_token(tmp_path, name="MCP reader", scopes=["intelligence:read"])
    other = create_token(tmp_path, name="Other", scopes=["cards:read"])
    with TestClient(app) as client:
        missing = _call(client, "tools/list")
        invalid = _call(client, "tools/list", token="bad-token")
        forbidden = _call(client, "tools/list", token=other["plaintext_token"])
        assert (missing.status_code, missing.json()["code"]) == (401, "missing_token")
        assert (invalid.status_code, invalid.json()["code"]) == (401, "invalid_token")
        assert forbidden.status_code == 200  # tool-level scopes, after Bearer authentication

        token = reader["plaintext_token"]
        tools = _call(client, "tools/list", token=token)
        assert tools.status_code == 200
        assert {tool["name"] for tool in tools.json()["result"]["tools"]} == {"get_card", "search", "get_profile", "push_profile", "get_daily_brief", "get_updates_since", "get_evaluation_rubric", "list_new_cards", "get_cards", "submit_evaluations", "get_source_stats", "get_collect_capabilities", "start_research", "get_research_run", "capture_url", "get_capture", "get_source_capabilities", "list_sources", "create_source", "update_source", "set_source_enabled", "list_source_changes", "get_opportunity_lens", "list_opportunity_candidates", "submit_opportunity_breakdowns", "list_opportunities", "submit_verification", "list_verifications", "set_opportunity_follow_up", "get_knowledge_schema", "list_extraction_candidates", "search_knowledge_cards", "submit_knowledge_extractions", "list_knowledge_cards", "get_knowledge_card", "set_research_status"}
        empty_brief = _call(client, "tools/call", {"name": "get_daily_brief", "arguments": {}}, token)
        assert empty_brief.json()["result"]["structuredContent"]["status"] == "not_generated"
        updates = _call(client, "tools/call", {"name": "get_updates_since", "arguments": {
            "since": "2026-07-01T00:00:00Z"}}, token)
        assert updates.json()["result"]["structuredContent"]["related"] == []
        card = _call(client, "tools/call", {"name": "get_card", "arguments": {"card_id": "card-one"}}, token)
        assert card.status_code == 200
        card_data = card.json()["result"]["structuredContent"]
        assert card_data["content_blocks"][0]["block_kind"] == "card_content"
        assert {block["block_kind"] for block in card_data["content_blocks"]} >= {"intel_summary", "relation_note"}
        assert "intelligence_status" not in card_data
        assert card_data["evaluation"] is None
        assert card_data["content"] == "Research body from block"
        assert card_data["missing_reasons"] == ["image_missing"]
        assert card_data["assets"][0]["url"] == "/api/card-preview/assets/asset-one"
        assert card_data["deep_link"] == {"url": "/r/card/card-one", "relative_url": True}

        search = _call(client, "tools/call", {"name": "search", "arguments": {
            "query": "Research body", "platform": "rss", "since": "2026-07-01T00:00:00Z",
        }}, token)
        assert search.status_code == 200
        results = search.json()["result"]["structuredContent"]["items"]
        assert [item["card_id"] for item in results] == ["card-one"]
        assert results[0]["deep_link"]["url"] == "/r/card/card-one"
        assert results[0]["deep_link"]["relative_url"] is True
        assert _call(client, "tools/call", {"name": "search", "arguments": {
            "query": "Research body", "until": "2026-07-01T00:00:00Z",
        }}, token).json()["result"]["structuredContent"]["items"] == []

        monkeypatch.setenv("INFOOS_PUBLIC_BASE_URL", "https://infoos.example.com")
        absolute = _call(client, "tools/call", {"name": "get_card", "arguments": {
            "card_id": "card-one",
        }}, token).json()["result"]["structuredContent"]
        assert absolute["deep_link"] == {
            "url": "https://infoos.example.com/r/card/card-one", "relative_url": False,
        }
        assert absolute["assets"][0]["url"] == "https://infoos.example.com/api/card-preview/assets/asset-one"

        cross_origin = client.post("/mcp", headers={
            "Authorization": f"Bearer {token}", "Origin": "https://elsewhere.test",
            "Accept": "application/json, text/event-stream",
        }, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert cross_origin.status_code == 200

    audit = ApiRequestLogRepository(tmp_path).get_detail(card.headers["x-request-id"])
    assert audit["path"] == "/mcp"
    assert audit["token_prefix"] == reader["token"]["token_prefix"]
    assert audit["response_meta"]["required_scope"] == "intelligence:read"
    assert ApiRequestLogRepository(tmp_path).get_detail(forbidden.headers["x-request-id"])["result_code"] == "ok"


def test_mcp_card_content_falls_back_to_card_body_like_the_reader(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFOOS_DATA_HOME", str(tmp_path / "data"))
    app = create_app(tmp_path)
    CardRepository(tmp_path).upsert_card({
        "card_id": "card-body", "card_type": "information", "version": 1,
        "content_hash": "sha256:" + "b" * 64, "status": "active",
        "completeness_status": "complete", "missing_reasons": [],
        "source_platform": "github", "source_type": "issue",
        "source_url": "https://example.test/issue", "source_author": "Author",
        "title": "Issue", "content": "Body stored only on the card",
        "published_at": "2026-07-16T00:00:00Z", "updated_at": "2026-07-16T01:00:00Z",
        "captured_at": "2026-07-16T00:10:00Z", "processed_at": "2026-07-16T01:00:00Z",
    })
    CardContentBlockRepository(tmp_path).upsert_block({
        "block_id": "comments", "card_id": "card-body", "position": 1,
        "layer": "context", "block_kind": "platform_comments", "status": "preserved",
        "body": "A comment",
    })
    token = create_token(tmp_path, name="MCP reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(app) as client:
        single = _call(client, "tools/call", {"name": "get_card", "arguments": {"card_id": "card-body"}}, token)
        batch = _call(client, "tools/call", {"name": "get_cards", "arguments": {"card_ids": ["card-body"]}}, token)
    assert single.json()["result"]["structuredContent"]["content"] == "Body stored only on the card"
    assert batch.json()["result"]["structuredContent"]["items"][0]["content"] == "Body stored only on the card"


def test_mcp_source_stats_scope_validation_and_structure(tmp_path, monkeypatch) -> None:
    from tests.infoos.test_source_stats import START, END, _source, _source_card
    from backend.infrastructure.storage.database import default_database_path

    monkeypatch.setenv("INFOOS_DATA_HOME", str(tmp_path / "data"))
    app = create_app(tmp_path)
    _source(tmp_path, "health")
    _source_card(default_database_path(tmp_path), 1, "health")
    reader = create_token(tmp_path, name="Health reader", scopes=["intelligence:read"])["plaintext_token"]
    other = create_token(tmp_path, name="Other", scopes=["cards:read"])["plaintext_token"]
    with TestClient(app) as client:
        def stats(arguments, token=reader):
            return _call(client, "tools/call", {"name": "get_source_stats", "arguments": arguments}, token).json()["result"]

        denied = stats({}, other)
        assert denied["isError"] is True
        assert "insufficient_scope" in denied["content"][0]["text"]
        for arguments in ({"since": "2026-09-01"}, {"until": "bad"}, {"since": ""},
                          {"since": END, "until": START}, {"min_evaluated": 0}, {"min_evaluated": 1001}):
            assert stats(arguments)["isError"] is True
        result = stats({"since": START, "until": END, "min_evaluated": 1})["structuredContent"]
        assert set(result) == {"since", "until", "min_evaluated", "quality_threshold", "totals", "items"}
        assert result["totals"] == {"cards": 1, "evaluated": 1, "passed": 1, "pass_rate": 1.0}
        assert result["items"][0]["source_id"] == "health"
        assert result["items"][0]["verdict"] == "high_yield"
        assert stats({})["structuredContent"]["min_evaluated"] == 10
        tools = _call(client, "tools/list", token=reader).json()["result"]["tools"]
        description = next(tool["description"] for tool in tools if tool["name"] == "get_source_stats")
        assert "必须先问用户" in description
        assert "不会自动修改来源配置" in description


def test_mcp_schema_snapshot_and_worker_thread(tmp_path, monkeypatch):
    import threading
    import backend.api.mcp.server as module

    expected = {
        "get_knowledge_schema": (set(), []),
        "list_extraction_candidates": ({"since", "until", "limit", "cursor"}, ["since"]),
        "search_knowledge_cards": ({"kind", "query", "limit"}, ["kind", "query"]),
        "submit_knowledge_extractions": ({"evaluator", "items"}, ["evaluator", "items"]),
        "list_knowledge_cards": ({"kind", "since", "research_status", "attribution_ref", "limit", "cursor"}, []),
        "get_knowledge_card": ({"knowledge_id"}, ["knowledge_id"]),
        "set_research_status": ({"knowledge_id", "status", "note"}, ["knowledge_id", "status"]),
        "get_card": ({"card_id"}, ["card_id"]),
        "search": ({"query", "platform", "since", "until", "limit"}, ["query"]),
        "push_profile": ({"markdown"}, ["markdown"]),
        "get_profile": ({"version"}, []),
        "get_daily_brief": ({"date"}, []),
        "get_updates_since": ({"since", "limit"}, ["since"]),
        "get_evaluation_rubric": (set(), []),
        "list_new_cards": ({"since", "until", "limit", "cursor", "only_unevaluated", "platform"}, ["since"]),
        "get_cards": ({"card_ids"}, ["card_ids"]),
        "submit_evaluations": ({"evaluator", "items"}, ["evaluator", "items"]),
        "get_opportunity_lens": (set(), []),
        "list_opportunity_candidates": ({"since", "until", "limit", "cursor", "only_unbroken"}, ["since"]),
        "submit_opportunity_breakdowns": ({"evaluator", "items"}, ["evaluator", "items"]),
        "list_opportunities": ({"since", "next_step", "min_replicability", "limit", "cursor"}, []),
        "submit_verification": ({"card_id", "recorder", "contact_channel", "offer", "credibility", "conclusion", "contacted_on", "price", "delivery", "funnel", "doubts", "replicability", "note", "supersedes", "client_verification_id", "breakdown_id", "expected_breakdown_hash"}, ["card_id", "recorder", "contact_channel", "offer", "credibility", "conclusion", "client_verification_id"]),
        "list_verifications": ({"card_id", "since", "conclusion", "limit", "cursor"}, []),
        "set_opportunity_follow_up": ({"card_id", "state", "reason"}, ["card_id", "state"]),
        "get_source_stats": ({"since", "until", "min_evaluated"}, []),
        "get_collect_capabilities": (set(), []),
        "start_research": ({"name", "keywords", "platforms", "date_range", "idempotency_key"}, ["name", "keywords", "platforms"]),
        "get_research_run": ({"research_run_id", "item_limit", "cursor"}, ["research_run_id"]),
        "capture_url": ({"url", "title", "client_capture_id", "refresh"}, ["url"]),
        "get_capture": ({"capture_id"}, ["capture_id"]),
        "get_source_capabilities": (set(), []),
        "list_sources": ({"platform", "enabled", "query", "limit", "offset"}, []),
        "create_source": ({"platform", "source_type", "identifier", "reason", "name", "options"}, ["platform", "source_type", "identifier", "reason"]),
        "update_source": ({"source_id", "reason", "changes"}, ["source_id", "reason", "changes"]),
        "set_source_enabled": ({"source_id", "enabled", "reason"}, ["source_id", "enabled", "reason"]),
        "list_source_changes": ({"source_id", "limit", "offset"}, []),
    }
    threads = []
    def read(*args):
        threads.append(threading.get_ident())
        return None
    monkeypatch.setattr(module, "read_plugin_card_detail", read)
    monkeypatch.setattr(module, "read_card_preview_detail", lambda *args: None)
    token = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(create_app(tmp_path)) as client:
        loop_thread = client.portal.call(threading.get_ident)
        tools = _call(client, "tools/list", token=token).json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == set(expected)
        for tool in tools:
            schema = tool["inputSchema"]
            assert (set(schema["properties"]), schema.get("required", [])) == expected[tool["name"]]
            assert "ctx" not in schema["properties"]
        result = _call(client, "tools/call", {"name": "get_cards", "arguments": {
            "card_ids": ["missing"]}}, token).json()["result"]
        assert result["structuredContent"]["items"][0]["error"] == "card_not_found"
    assert len(threads) == 1 and threads[0] != loop_thread


def test_mcp_search_time_bounds_read_once(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import backend.api.mcp.server as module

    calls = []
    def read(base_dir, **kwargs):
        calls.append(kwargs)
        return {"items": [], "total": 1000}
    monkeypatch.setattr(module, "read_card_preview_page", read)
    token = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(create_app(tmp_path)) as client:
        result = _call(client, "tools/call", {"name": "search", "arguments": {
            "query": "research", "since": "2026-07-01T00:00:00Z",
            "until": "2026-07-02T00:00:00Z", "limit": 3}}, token).json()["result"]
    assert result["structuredContent"] == {"items": [], "limit": 3}
    assert calls == [{"query": "research", "platform": None, "limit": 3,
                      "published_since": datetime(2026, 7, 1, tzinfo=timezone.utc),
                      "published_until": datetime(2026, 7, 2, tzinfo=timezone.utc)}]


@pytest.mark.parametrize("scopes,denied", [(["intelligence:read"], False), (["cards:read"], True)])
def test_mcp_call_audits_tool_even_when_scope_denied(tmp_path, scopes, denied):
    token = create_token(tmp_path, name="audit-test", scopes=scopes)["plaintext_token"]
    with TestClient(create_app(tmp_path)) as client:
        response = _call(client, "tools/call", {"name": "get_daily_brief", "arguments": {}}, token)
    assert response.json()["result"].get("isError", False) is denied
    audit = ApiRequestLogRepository(tmp_path).get_detail(response.headers["x-request-id"])
    assert audit["response_meta"]["mcp_tool"] == "get_daily_brief"
