"""Authenticated source tools, shared Web validation, and atomic audit evidence."""
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.planner import plan_fetch_tasks_for_source
from backend.application.usecases.api_tokens import create_token
from backend.application.usecases.source_configuration_lifecycle import create_source_configuration
from backend.infrastructure.storage.database import connect
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.sources import SourceRepository
from tests.infoos.test_mcp_collect import call, invoke

REQUEST = {"platform": "x", "source_type": "creator", "identifier": "alice", "reason": "请添加这个账号"}
WEB = {"platform": "x", "source_type": "creator", "identifier": "alice", "display_name": "alice",
       "cadence_hours": 6, "max_items_per_run": 20}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("INFOOS_PUBLIC_BASE_URL", raising=False)
    reader = create_token(tmp_path, name="reader", scopes=["intelligence:read"])
    writer = create_token(tmp_path, name="writer", scopes=["intelligence:read", "sources:write"])
    only_writer = create_token(tmp_path, name="only-writer", scopes=["sources:write"])
    with TestClient(create_app(tmp_path)) as client:
        yield client, reader, writer, only_writer


def test_scopes_and_capabilities(setup):
    client, reader, _, only_writer = setup
    for name, arguments in [("create_source", REQUEST),
                            ("update_source", {"source_id": "id", "reason": "更新", "changes": {}}),
                            ("set_source_enabled", {"source_id": "id", "reason": "停用", "enabled": False})]:
        denied = invoke(client, reader, name, **arguments)
        assert denied["isError"] and "insufficient_scope" in denied["content"][0]["text"]
    for name in ("get_source_capabilities", "list_sources", "list_source_changes"):
        assert invoke(client, only_writer, name)["isError"]
        assert "error" not in call(client, reader, name)
    platforms = call(client, reader, "get_source_capabilities")["platforms"]
    assert len(platforms) == 10
    assert not {"google", "web", "wechat"} & {p["platform"] for p in platforms}
    assert all(p["source_types"] and "research" not in p for p in platforms)
    assert all("default_cadence_hours" in t and "max_items_limit" in t and "identifier_hint" in t
               for p in platforms for t in p["source_types"])


def test_create_duplicate_update_activation_and_audit(setup, tmp_path):
    client, reader, writer, _ = setup
    created = call(client, writer, "create_source", **REQUEST)
    sid = created["source_id"]
    assert created["task_id"] and created["initial_fetch"]
    audit = call(client, reader, "list_source_changes", source_id=sid)
    assert audit["total"] == 1
    first = audit["items"][0]
    assert (first["action"], first["actor_kind"], first["token_id"], first["reason"]) == (
        "create", "mcp", writer["token"]["token_id"], REQUEST["reason"])
    assert first["before_config_version"] is None and first["after_config_version"].startswith("sha256:")
    duplicate = call(client, writer, "create_source", **{**REQUEST, "identifier": " ALICE "})
    assert duplicate["error"] == "duplicate_source" and duplicate["existing_source_id"] == sid
    assert "error" not in call(client, writer, "update_source", source_id=sid, reason="改名和频率",
                               changes={"display_name": "Alice AI", "cadence_hours": 24, "max_items_per_run": 5})
    row = SourceRepository(tmp_path).get_source(sid)
    assert row["display_name"] == "Alice AI" and row["raw_metadata"]["max_items_per_run"] == 5
    update = call(client, reader, "list_source_changes", source_id=sid)["items"][0]
    assert update["action"] == "update" and update["before_config_version"] == first["after_config_version"]
    assert update["after_config_version"] != update["before_config_version"]
    stopped = call(client, writer, "set_source_enabled", source_id=sid, enabled=False, reason="请停掉")
    assert not stopped["source"]["enabled"] and stopped["task_id"] is None
    assert plan_fetch_tasks_for_source(tmp_path, SourceRepository(tmp_path).get_source(sid)) == []
    page = call(client, reader, "list_sources", enabled=False, query="Alice AI", platform="x")
    assert page["total"] == 1 and page["items"][0]["source_id"] == sid
    assert {"latest_run_at", "latest_run_status", "next_run_at"} <= page["items"][0].keys()
    assert call(client, reader, "list_sources", enabled=True)["total"] == 0
    call(client, writer, "set_source_enabled", source_id=sid, enabled=True, reason="重新启用")
    rows = call(client, reader, "list_source_changes", source_id=sid)["items"]
    assert [r["action"] for r in rows] == ["enable", "disable", "update", "create"]
    assert all(rows[i]["before_config_version"] == rows[i+1]["after_config_version"] for i in range(3))
    assert call(client, reader, "list_source_changes", limit=1, offset=1)["items"] == rows[1:2]


@pytest.mark.parametrize("changes", [
    {"platform": "web"}, {"platform": "google"}, {"platform": "wechat"}, {"source_type": "invented"},
    {"platform": "xiaohongshu", "source_type": "keyword", "identifier": ""},
    {"platform": "producthunt", "source_type": "feed", "identifier": "invalid"},
])
def test_validation_matches_web(setup, changes):
    client, _, writer, _ = setup
    result = call(client, writer, "create_source", **{**REQUEST, **changes}, options={"cadence_hours": 24})
    web = client.post("/api/configurable-sources", json={**WEB, "cadence_hours": 24, **changes})
    assert result["error"] == "invalid_source_configuration"
    assert web.status_code == 422 and web.json()["code"] == result["error"]
    assert result["details"] == web.json()["message"]


def test_invalid_reason_options_pagination_and_immutable_identity(setup):
    client, reader, writer, _ = setup
    for reason in ("", " ", "字" * 501):
        assert call(client, writer, "create_source", **{**REQUEST, "reason": reason})["error"] == "invalid_source_configuration"
    for options in ({"source_scope": "research"}, {"raw_metadata": {"system_managed": True}}, {"max_items_per_run": 100001}):
        assert call(client, writer, "create_source", **REQUEST, options=options)["error"] == "invalid_source_configuration"
    sid = call(client, writer, "create_source", **REQUEST)["source_id"]
    for changes in ({"source_id": "evil"}, {"source_scope": "research"}, {"raw_metadata": {"system_managed": True}}):
        assert call(client, writer, "update_source", source_id=sid, reason="修改", changes=changes)["error"] == "invalid_source_configuration"
    assert call(client, writer, "update_source", source_id=sid, reason="换账号", changes={"identifier": "bob"})["error"] == "immutable_source_identity"
    for tool in ("list_sources", "list_source_changes"):
        for args in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
            assert call(client, reader, tool, **args)["error"] == "invalid_pagination"
    assert call(client, reader, "list_source_changes")["total"] == 1


def test_internal_sources_are_hidden_and_not_editable(setup, tmp_path):
    client, reader, writer, _ = setup
    repo = SourceRepository(tmp_path)
    for sid, extra in (("research", {"source_scope": "research"}),
                       ("capture", {"raw_metadata": {"system_managed": True}})):
        repo.upsert_source({**WEB, "source_id": sid, "identifier": sid, **extra})
        assert call(client, writer, "update_source", source_id=sid, reason="改名", changes={"display_name": "新名称"})["error"] == "source_not_editable"
        assert call(client, writer, "set_source_enabled", source_id=sid, enabled=False, reason="停用")["error"] == "source_not_editable"
    assert call(client, reader, "list_sources")["total"] == 0
    assert call(client, reader, "list_source_changes")["total"] == 0


def test_web_paths_are_audited(setup):
    client, reader, _, _ = setup
    created = client.post("/api/configurable-sources", json=WEB)
    assert created.status_code == 201
    sid = created.json()["source_id"]
    assert client.put(f"/api/configurable-sources/{sid}", json={**WEB, "display_name": "更新"}).status_code == 200
    assert client.patch(f"/api/configurable-sources/{sid}/enabled", json={"enabled": False}).status_code == 200
    rows = call(client, reader, "list_source_changes", source_id=sid)["items"]
    assert [r["action"] for r in rows] == ["disable", "update", "create"]
    assert all(r["actor_kind"] == "web" and r["token_id"] is None and r["reason"] is None for r in rows)


def test_audit_failure_rolls_back_configuration_versions_and_queue(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit failed")
    monkeypatch.setattr("backend.application.usecases.source_configuration_lifecycle.append_source_change", fail)
    with pytest.raises(RuntimeError, match="audit failed"):
        create_source_configuration(tmp_path, WEB, now="2026-10-14T00:00:00Z")
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        for table in ("sources", "source_config_versions", "tasks", "source_change_audit"):
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] == 0
