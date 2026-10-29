from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.usecases.api_tokens import create_token
from backend.application.usecases.interest_profiles import push_profile
from backend.domain.intelligence.profile import ProfileValidationError, parse_profile
from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database


PROFILE = """## 主线与KR
- 提升情报质量 [[KR-1]]
## 进行中项目
- InfoOS [[决策-1]]
## 正在追问的问题
- 哪些内容值得读？
## 暂放方向
## 长期领域
- AI 影像
"""


def _mcp(client, token, name, arguments=None):
    return client.post("/mcp", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
    }, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": name, "arguments": arguments or {},
    }})


def test_profile_parser_and_hash_deduplication(tmp_path):
    assert parse_profile(PROFILE)["主线与KR"] == ["提升情报质量 [[KR-1]]"]
    assert parse_profile(PROFILE)["暂放方向"] == []
    with pytest.raises(ProfileValidationError, match="长期领域"):
        parse_profile(PROFILE.split("## 长期领域")[0])
    with pytest.raises(ProfileValidationError, match="20,000"):
        parse_profile(PROFILE + "x" * 20_000)
    migrate_database(default_database_path(tmp_path))
    first = push_profile(tmp_path, PROFILE, source="mcp:one")
    again = push_profile(tmp_path, PROFILE, source="mcp:two")
    changed = push_profile(tmp_path, PROFILE + "\n说明文字", source="mcp:one")
    assert (first["profile_version"], first["created"]) == (1, True)
    assert (again["profile_version"], again["created"]) == (1, False)
    assert (changed["profile_version"], changed["created"]) == (2, True)
    assert InterestProfileRepository(tmp_path).get(1)["markdown"] == PROFILE


def test_migration_empty_v7_and_repeated_start(tmp_path):
    path = default_database_path(tmp_path)
    fresh = migrate_database(path)
    assert fresh.after_version == LATEST_VERSION
    with sqlite3.connect(path) as conn:
        for table in ("interest_profiles", "card_evaluations", "daily_briefs"):
            assert conn.execute("select name from sqlite_schema where name = ?", (table,)).fetchone()
    assert migrate_database(path).before_version == LATEST_VERSION
    old_path = tmp_path / "v7" / "infoos.sqlite3"

    def stop_at_v8(event):
        if event["migration"] == 8:
            raise RuntimeError("simulate pre-v8 installation")

    with pytest.raises(RuntimeError, match="pre-v8"):
        migrate_database(old_path, progress_callback=stop_at_v8)
    with sqlite3.connect(old_path) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 7
        assert conn.execute("select name from sqlite_schema where name = 'interest_profiles'").fetchone() is None
    assert migrate_database(old_path).before_version == 7
    assert migrate_database(old_path).after_version == LATEST_VERSION


def test_http_auth_validation_history_and_audit(tmp_path):
    app = create_app(tmp_path)
    writer = create_token(tmp_path, name="writer", scopes=["profile:write"])["plaintext_token"]
    reader = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(app) as client:
        assert client.put("/api/intelligence/profile", json={"markdown": PROFILE}).status_code == 401
        assert client.put("/api/intelligence/profile", headers={"Authorization": f"Bearer {reader}"}, json={"markdown": PROFILE}).status_code == 403
        assert client.get("/api/intelligence/profile", headers={"Authorization": f"Bearer {writer}"}).status_code == 403
        bad = client.put("/api/intelligence/profile", headers={"Authorization": f"Bearer {writer}"}, json={"markdown": "## 主线与KR\n- x"})
        assert bad.status_code == 422 and "长期领域" in bad.json()["message"]
        good = client.put("/api/intelligence/profile", headers={"Authorization": f"Bearer {writer}", "Origin": "https://elsewhere.test"}, json={"markdown": PROFILE})
        assert good.status_code == 200 and good.json()["profile_version"] == 1
        same = client.put("/api/intelligence/profile", headers={"Authorization": f"Bearer {writer}"}, json={"markdown": PROFILE})
        assert same.json()["created"] is False
        current = client.get("/api/intelligence/profile", headers={"Authorization": f"Bearer {reader}"})
        assert current.status_code == 200 and current.json()["sections"]["长期领域"] == ["AI 影像"]
        history = client.get("/api/intelligence/profile?version=1", headers={"Authorization": f"Bearer {reader}"})
        assert history.status_code == 200
        assert client.get("/api/intelligence/profile?version=99", headers={"Authorization": f"Bearer {reader}"}).status_code == 404
    audit = ApiRequestLogRepository(tmp_path).get_detail(good.headers["x-request-id"])
    assert audit["path"] == "/api/intelligence/profile" and audit["response_meta"]["required_scope"] == "profile:write"


def test_mcp_profile_scopes(tmp_path):
    app = create_app(tmp_path)
    writer = create_token(tmp_path, name="writer", scopes=["profile:write"])["plaintext_token"]
    reader = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(app) as client:
        denied = _mcp(client, reader, "push_profile", {"markdown": PROFILE})
        assert denied.status_code == 200
        assert denied.json()["result"]["isError"] is True
        assert "profile:write" in str(denied.json()["result"])
        pushed = _mcp(client, writer, "push_profile", {"markdown": PROFILE})
        assert pushed.status_code == 200
        assert pushed.json()["result"]["structuredContent"]["created"] is True
        assert pushed.json()["result"]["structuredContent"]["section_counts"]["暂放方向"] == 0
        read = _mcp(client, reader, "get_profile", {"version": 1})
        assert read.json()["result"]["structuredContent"]["profile_version"] == 1
        rejected_read = _mcp(client, writer, "get_profile")
        assert rejected_read.json()["result"]["isError"] is True
        assert "intelligence:read" in str(rejected_read.json()["result"])


def test_profile_parser_keeps_items_under_subheadings():
    markdown = "\n".join([
        "# 关注画像",
        "## 主线与KR", "- AI 自媒体获客",
        "## 进行中项目", "### AI自媒体IP", "- 需要：选题与对标", "### InfoOS", "- 需要：情报系统案例",
        "## 正在追问的问题", "## 暂放方向", "## 长期领域", "- 人工智能",
    ])
    sections = parse_profile(markdown)
    assert sections["进行中项目"] == ["需要：选题与对标", "需要：情报系统案例"]
    assert sections["暂放方向"] == []
