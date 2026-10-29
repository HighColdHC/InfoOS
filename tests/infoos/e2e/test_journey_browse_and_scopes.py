"""漫步阅读与 tools/list 发现的全部工具最小权限矩阵。"""
from backend.application.usecases.api_tokens import create_token, revoke_token
from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository
from tests.infoos.e2e.support import (
    PROFILE, START, assert_denied, build_seeded, call, drain, evaluation, reader_get, seed_raw,
)
from tests.infoos.test_mcp_interface import _call
from tests.infoos.test_verification_domain import item as verification


def test_browse_journey(journey, tmp_path, monkeypatch):
    client, scheduled, _ = journey
    call(client, scheduled, "push_profile", markdown=PROFILE)
    for key in ("passed", "low", "unevaluated"):
        seed_raw(tmp_path, monkeypatch, key)
    cards = build_seeded(tmp_path)
    call(client, scheduled, "submit_evaluations", evaluator="journey", items=[
        evaluation(cards["passed"]), evaluation(cards["low"], quality=59)])
    drain(tmp_path)
    for view, expected in (("passed", {cards["passed"]}), ("evaluated", {cards["passed"], cards["low"]}),
                           ("unevaluated", {cards["unevaluated"]})):
        page = reader_get(client, monkeypatch, f"/api/card-preview?evaluation={view}").json()
        assert page["total"] == len(expected)
        assert {r["card_id"] for r in page["items"]} == expected
    facets = reader_get(client, monkeypatch, "/api/card-preview/facets?scope=maintained").json()
    assert [(p["platform"], p["card_count"]) for p in facets["platforms"]] == [("rss", 3)]
    assert len(facets["sources"]) == 1
    source = facets["sources"][0]
    assert (source["card_count"], source["evaluated_count"], source["passed_count"]) == (3, 2, 1)
    for view, total in (("passed", 1), ("evaluated", 2), ("unevaluated", 1)):
        page = client.get("/api/card-preview", params={"source_id": source["source_id"], "evaluation": view}).json()
        assert page["total"] == total
    assert reader_get(client, monkeypatch, "/r/browse").headers["content-type"].startswith("text/html")


# Explicit independent expectation: adding a tool requires assigning its scope here.
READ = {"get_knowledge_schema", "list_extraction_candidates", "search_knowledge_cards", "list_knowledge_cards", "get_knowledge_card","get_card", "search", "get_profile", "get_daily_brief", "get_updates_since", "get_evaluation_rubric",
        "list_new_cards", "get_cards", "get_source_stats", "get_collect_capabilities", "get_research_run",
        "get_source_capabilities", "list_sources", "list_source_changes", "get_opportunity_lens",
        "list_opportunity_candidates", "list_opportunities", "list_verifications"}
SCOPES = {"submit_knowledge_extractions": "evaluation:write", "set_research_status": "verification:write",**dict.fromkeys(READ, "intelligence:read"), "push_profile": "profile:write",
          "submit_evaluations": "evaluation:write", "submit_opportunity_breakdowns": "evaluation:write",
          "start_research": "collect:write", "capture_url": "collect:write", "get_capture": "collect:write",
          "create_source": "sources:write", "update_source": "sources:write", "set_source_enabled": "sources:write",
          "submit_verification": "verification:write", "set_opportunity_follow_up": "verification:write"}
ARGUMENTS = {
    "list_extraction_candidates": {"since": START},
    "search_knowledge_cards": {"kind": "project", "query": "主体"},
    "submit_knowledge_extractions": {"evaluator": "journey", "items": [{"card_id": "missing"}]},
    "get_knowledge_card": {"knowledge_id": "missing"},
    "set_research_status": {"knowledge_id": "missing", "status": "observe"},
    "get_card": {"card_id": "missing"}, "search": {"query": "场景"}, "push_profile": {"markdown": PROFILE},
    "get_updates_since": {"since": START}, "list_new_cards": {"since": START}, "get_cards": {"card_ids": ["missing"]},
    "submit_evaluations": {"evaluator": "journey", "items": [evaluation("missing")]},
    "list_opportunity_candidates": {"since": START},
    "submit_opportunity_breakdowns": {"evaluator": "journey", "items": [{"card_id": "missing"}]},
    "submit_verification": verification(card_id="missing"),
    "set_opportunity_follow_up": {"card_id": "missing", "state": "following"},
    "get_research_run": {"research_run_id": "missing"}, "get_capture": {"capture_id": "missing"},
    "capture_url": {"url": "https://8.8.8.8/journey"},
    "start_research": {"name": "权限矩阵", "keywords": ["AI"], "platforms": [{"platform": "github", "max_items": 1}]},
    "create_source": {"platform": "rss", "source_type": "feed", "identifier": "https://example.test/scope.xml", "reason": "添加"},
    "update_source": {"source_id": "missing", "reason": "改名", "changes": {"display_name": "新名称"}},
    "set_source_enabled": {"source_id": "missing", "reason": "停用", "enabled": False},
}


def test_all_discovered_tools_enforce_their_minimum_scope(journey, tmp_path, monkeypatch):
    client, scheduled, interactive = journey
    monkeypatch.setattr("backend.application.usecases.capture_policy.select_supported_proxy", lambda url: None)
    call(client, scheduled, "push_profile", markdown=PROFILE)
    assert _call(client, "tools/list").status_code == 401
    discovered = _call(client, "tools/list", token=interactive["plaintext_token"]).json()["result"]["tools"]
    assert {t["name"] for t in discovered} == set(SCOPES)
    single = {scope: create_token(tmp_path, name=scope, scopes=[scope]) for scope in set(SCOPES.values())}
    unrelated = create_token(tmp_path, name="无相关权限", scopes=["cards:read"])
    try:
        for tool in discovered:
            name = tool["name"]
            scope = SCOPES[name]
            args = ARGUMENTS.get(name, {})
            params = {"name": name, "arguments": args}
            assert _call(client, "tools/call", params).status_code == 401
            # Every other single-scope token is insufficient, including read vs write.
            for token in [unrelated, *(t for s, t in single.items() if s != scope)]:
                assert_denied(client, token, name, **args)
            response = _call(client, "tools/call", params, token=single[scope]["plaintext_token"])
            assert response.status_code == 200
            result = response.json()["result"]
            assert "insufficient_scope" not in str(result), result
            audit = ApiRequestLogRepository(tmp_path).get_detail(response.headers["x-request-id"])
            assert audit["response_meta"]["required_scope"] == scope
            if scope in {"collect:write", "sources:write", "verification:write"}:
                assert_denied(client, scheduled, name, **args)
    finally:
        for token in [*single.values(), unrelated]:
            revoke_token(tmp_path, token["token"]["token_id"])
