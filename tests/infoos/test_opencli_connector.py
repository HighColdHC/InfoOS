import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.workflows.fetch import fetch, main as fetch_main
from backend.application.usecases.source_config import load_source_configs
from backend.infrastructure.connectors.opencli import OpenCLIConnector
from tests.infoos._factories import normalize_opencli_command


def _completed(stdout: str, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_opencli_connector_parses_top_level_json_list(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _completed(
            json.dumps(
                [
                    {
                        "id": "hn-1",
                        "title": "Agent roadmap update",
                        "url": "https://news.ycombinator.com/item?id=1",
                        "author": {"name": "Alice"},
                        "created_at": "2026-04-24T09:00:00Z",
                    }
                ]
            )
        )

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_hn",
        command=["hackernews", "top", "--limit", "1"],
        source_platform="hackernews",
        source_type="article",
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert [normalize_opencli_command(call) for call in calls] == [
        ["opencli", "hackernews", "top", "--limit", "1", "--format", "json", "--window", "background"]
    ]
    assert result.next_cursor == "hn-1"
    assert len(result.items) == 1
    item = result.items[0]
    assert item.raw_item_id == "hn-1"
    assert item.source_platform == "hackernews"
    assert item.source_type == "article"
    assert item.source_url == "https://news.ycombinator.com/item?id=1"
    assert item.raw_text == "Agent roadmap update"
    assert item.raw_author.name == "Alice"
    assert "title" not in (item.raw_metadata or {})
    assert item.raw_metadata["opencli_record"]["id"] == "hn-1"


def test_opencli_connector_can_map_real_platform_title_to_metadata(monkeypatch):
    def fake_run(command, **kwargs):
        return _completed(
            json.dumps(
                [
                    {
                        "id": "hn-1",
                        "title": "Agent roadmap update",
                        "url": "https://news.ycombinator.com/item?id=1",
                    }
                ]
            )
        )

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_hn",
        command=["hackernews", "top", "--limit", "1"],
        source_platform="hackernews",
        source_type="story",
        metadata_mapping={"title": "title"},
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert result.items[0].raw_text == "Agent roadmap update"
    assert result.items[0].raw_metadata["title"] == "Agent roadmap update"


def test_opencli_connector_can_enrich_records_with_detail_command(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        normalized = normalize_opencli_command(command)
        if normalized[3:6] == ["xiaohongshu", "search", "AI"]:
            return _completed(
                json.dumps(
                    [
                        {
                            "title": "保姆级教程从0部署一个能卖给企业的智能体",
                            "url": "https://www.xiaohongshu.com/search_result/1",
                            "author": "示例作者",
                        }
                    ]
                )
            )
        if normalized[3:5] == ["xiaohongshu", "note"]:
            return _completed(
                json.dumps(
                    [
                        {"field": "title", "value": "保姆级教程从0部署一个能卖给企业的智能体"},
                        {
                            "field": "content",
                            "value": "AI时代的新风口 Agent搭建师，一条视频教会你如何从0部署一个能卖给企业的Agent。",
                        },
                        {"field": "likes", "value": "1705"},
                        {"field": "tags", "value": "#Agent搭建师, #企业AI"},
                    ]
                )
            )
        raise AssertionError(f"unexpected command: {normalized}")

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_xhs",
        command=["xiaohongshu", "search", "AI", "--limit", "1"],
        source_platform="xiaohongshu",
        source_type="social_post",
        keep_alive=False,
        field_mapping={
            "id": "url",
            "title": "title",
            "url": "url",
            "text": "content",
            "author_name": "author",
        },
        metadata_mapping={"likes": "likes", "tags": "tags"},
        detail_command_template=["xiaohongshu", "note", "{url}"],
        detail_max_items=1,
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert [normalize_opencli_command(call)[:6] for call in calls] == [
        ["opencli", "--profile", "infoos-fetch", "xiaohongshu", "search", "AI"],
        ["opencli", "--profile", "infoos-fetch", "xiaohongshu", "note", "https://www.xiaohongshu.com/search_result/1"],
    ]
    for call in calls:
        normalized = normalize_opencli_command(call)
        assert normalized[normalized.index("--site-session") + 1] == "persistent"
        assert normalized[normalized.index("--keep-tab") + 1] == "false"
    item = result.items[0]
    assert item.raw_text == "AI时代的新风口 Agent搭建师，一条视频教会你如何从0部署一个能卖给企业的Agent。"
    assert item.raw_metadata["likes"] == "1705"
    assert item.raw_metadata["tags"] == "#Agent搭建师, #企业AI"
    assert item.raw_metadata["opencli_record"]["content"] == item.raw_text


def test_opencli_connector_keeps_basic_record_when_detail_command_fails(monkeypatch):
    def fake_run(command, **kwargs):
        normalized = normalize_opencli_command(command)
        if normalized[3:6] == ["xiaohongshu", "search", "AI"]:
            return _completed(
                json.dumps(
                    [
                        {
                            "title": "基础搜索结果",
                            "content": "搜索页正文",
                            "url": "https://www.xiaohongshu.com/search_result/1?xsec_token=abc",
                            "author": "作者",
                        }
                    ]
                )
            )
        if normalized[3:5] == ["xiaohongshu", "note"]:
            return _completed("", returncode=1, stderr="AUTH_REQUIRED")
        raise AssertionError(f"unexpected command: {normalized}")

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_xhs",
        command=["xiaohongshu", "search", "AI", "--limit", "1"],
        source_platform="xiaohongshu",
        source_type="social_post",
        keep_alive=False,
        field_mapping={
            "id": "url",
            "title": "title",
            "url": "url",
            "text": "content",
            "author_name": "author",
        },
        metadata_mapping={"note_url": "url"},
        detail_command_template=["xiaohongshu", "note", "{url}"],
        detail_max_items=1,
    )

    result = connector.fetch_new_items(since_cursor=None)

    item = result.items[0]
    assert item.raw_text == "搜索页正文"
    assert item.source_url == "https://www.xiaohongshu.com/search_result/1?xsec_token=abc"
    assert item.raw_metadata["note_url"] == "https://www.xiaohongshu.com/search_result/1?xsec_token=abc"
    assert item.raw_metadata["opencli_record"]["_opencli_detail_error"]["error"].startswith(
        "OpenCLI command failed"
    )


def test_opencli_connector_continues_fetch_when_detail_subprocess_times_out(monkeypatch):
    connector = OpenCLIConnector(
        connector_id="opencli_hn",
        command=["hackernews", "top"],
        source_platform="hackernews",
        source_type="article",
        detail_command_template=["hackernews", "item", "{id}"],
    )
    calls = []
    timeout = subprocess.TimeoutExpired(cmd="opencli hackernews item hn-1", timeout=60)

    def load_payload(command=None):
        # Inject the raw subprocess exception at the payload boundary so this
        # verifies detail isolation independently of transport normalization.
        calls.append(command)
        if command is None:
            return [
                {"id": "hn-1", "title": "First", "url": "https://news.ycombinator.com/item?id=1"},
                {"id": "hn-2", "title": "Second", "url": "https://news.ycombinator.com/item?id=2"},
            ]
        if command[-1] == "hn-1":
            raise timeout
        return {"title": "Second detail"}

    monkeypatch.setattr(connector, "_load_payload", load_payload)

    result = connector.fetch_new_items(since_cursor=None)

    assert [item.raw_item_id for item in result.items] == ["hn-1", "hn-2"]
    assert result.items[0].raw_text == "First"
    error = result.items[0].raw_metadata["opencli_record"]["_opencli_detail_error"]
    assert error == {"command": ["hackernews", "item", "hn-1"], "error": str(timeout)}
    assert result.items[1].raw_text == "Second detail"
    assert calls == [None, ["hackernews", "item", "hn-1"], ["hackernews", "item", "hn-2"]]


def test_opencli_connector_only_detail_enriches_items_newer_than_cursor(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        normalized = normalize_opencli_command(command)
        if normalized[3:6] == ["xiaohongshu", "search", "AI"]:
            return _completed(
                json.dumps(
                    [
                        {"title": "Newest", "url": "https://www.xiaohongshu.com/explore/3"},
                        {"title": "Seen", "url": "https://www.xiaohongshu.com/explore/2"},
                        {"title": "Older", "url": "https://www.xiaohongshu.com/explore/1"},
                    ]
                )
            )
        if normalized[3:5] == ["xiaohongshu", "note"]:
            return _completed(
                json.dumps(
                    [
                        {"field": "content", "value": "Only the new note is hydrated"},
                    ]
                )
            )
        raise AssertionError(f"unexpected command: {normalized}")

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_xhs",
        command=["xiaohongshu", "search", "AI", "--limit", "3"],
        source_platform="xiaohongshu",
        source_type="social_post",
        result_order="newest_first",
        field_mapping={
            "id": "url",
            "title": "title",
            "url": "url",
            "text": "content",
        },
        detail_command_template=["xiaohongshu", "note", "{url}"],
        detail_max_items=20,
    )

    result = connector.fetch_new_items(
        since_cursor="https://www.xiaohongshu.com/explore/2"
    )

    assert len(calls) == 2
    assert normalize_opencli_command(calls[1])[3:6] == [
        "xiaohongshu",
        "note",
        "https://www.xiaohongshu.com/explore/3",
    ]
    assert [item.raw_item_id for item in result.items] == [
        "https://www.xiaohongshu.com/explore/3"
    ]
    assert result.items[0].raw_text == "Only the new note is hydrated"
    assert result.visible_count == 3
    assert result.new_count == 1
    assert result.dedup_skipped_count == 2


def test_opencli_connector_falls_back_when_configured_author_paths_are_stale(monkeypatch):
    def fake_run(command, **kwargs):
        return _completed(
            json.dumps(
                [
                    {
                        "id": "tweet-1",
                        "text": "RT @someone: current x shape",
                        "url": "https://x.com/elonmusk/status/1",
                        "author": "elonmusk",
                        "name": "Elon Musk",
                        "created_at": "Fri Jun 26 05:01:14 +0000 2026",
                    }
                ]
            )
        )

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_x",
        command=["twitter", "tweets", "elonmusk", "--limit", "1"],
        source_platform="x",
        source_type="social_post",
        field_mapping={
            "id": "id",
            "url": "url",
            "text": "text",
            "author_name": "author.name",
            "author_handle": "author.username",
            "published_at": "created_at",
        },
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert result.items[0].raw_author.name == "Elon Musk"
    assert result.items[0].raw_author.handle == "elonmusk"


def test_opencli_connector_resolves_stable_env_executable(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _completed(json.dumps([{"id": "hn-1", "title": "Stable path"}]))

    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "/stable/bin/opencli")
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(connector_id="opencli_hn", command=["hackernews", "top"])

    result = connector.fetch_new_items(since_cursor=None)

    assert result.items[0].raw_item_id == "hn-1"
    assert calls == [["/stable/bin/opencli", "hackernews", "top", "--format", "json", "--window", "background"]]


def test_opencli_connector_can_skip_browser_window_flag_for_public_commands(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _completed(json.dumps([{"id": "hn-1", "title": "Public command"}]))

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_hn",
        command=["hackernews", "top", "--limit", "1"],
        use_browser_window=False,
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert result.items[0].raw_item_id == "hn-1"
    assert [normalize_opencli_command(call) for call in calls] == [
        ["opencli", "hackernews", "top", "--limit", "1", "--format", "json"]
    ]


def test_opencli_connector_reports_visible_and_cursor_skipped_counts(monkeypatch):
    def fake_run(command, **kwargs):
        return _completed(
            json.dumps(
                [
                    {"id": "tweet-3", "text": "Newest", "url": "https://x.com/a/status/3"},
                    {"id": "tweet-2", "text": "Middle", "url": "https://x.com/a/status/2"},
                    {"id": "tweet-1", "text": "Seen", "url": "https://x.com/a/status/1"},
                ]
            )
        )

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_x",
        command=["twitter", "tweets", "elonmusk"],
        source_platform="x",
        source_type="social_post",
        result_order="newest_first",
    )

    result = connector.fetch_new_items(since_cursor="tweet-1")

    assert [item.raw_item_id for item in result.items] == ["tweet-3", "tweet-2"]
    assert result.visible_count == 3
    assert result.new_count == 2
    assert result.dedup_skipped_count == 1
    assert connector.discovery_evidence == {
        "executor": "opencli",
        "session_alias": "twitter",
        "sanitized_arguments": ["twitter", "tweets", "elonmusk"],
        "links": [
            "https://x.com/a/status/3",
            "https://x.com/a/status/2",
            "https://x.com/a/status/1",
        ],
        "discovered_link_count": 3,
        "availability": "available",
    }


def test_opencli_connector_serializes_browser_bridge_commands(monkeypatch):
    events = []

    class FakeLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")

    def fake_run(command, **kwargs):
        events.append(("run", command))
        return _completed(json.dumps([{"id": "x-1", "text": "Locked"}]))

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.opencli_tab_lease", lambda *_args, **_kwargs: FakeLock())
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_x", command=["twitter", "tweets", "elonmusk"], source_platform="x"
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert result.items[0].raw_item_id == "x-1"
    assert len(events) == 3
    assert events[0] == "enter"
    assert events[1][0] == "run"
    assert normalize_opencli_command(events[1][1]) == [
        "opencli",
        "--profile",
        "infoos-fetch",
        "twitter",
        "tweets",
        "elonmusk",
        "--format",
        "json",
        "--window",
        "background",
        "--site-session",
        "ephemeral",
        "--keep-tab",
        "false",
    ]
    assert events[2] == "exit"


def test_opencli_connector_parses_items_path_and_field_mapping(monkeypatch):
    def fake_run(command, **kwargs):
        return _completed(
            json.dumps(
                {
                    "payload": {
                        "entries": [
                            {
                                "post_id": "post-1",
                                "canonical": "https://example.com/post-1",
                                "body": "Mapped body",
                                "profile": {"display": "Bob", "handle": "bob"},
                                "published": "2026-04-24T10:00:00Z",
                            }
                        ]
                    }
                }
            )
        )

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_custom",
        command=["custom", "list", "-f", "json"],
        source_platform="custom",
        source_type="post",
        items_path="payload.entries",
        field_mapping={
            "id": "post_id",
            "url": "canonical",
            "text": "body",
            "author_name": "profile.display",
            "author_handle": "profile.handle",
            "published_at": "published",
        },
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.raw_item_id == "post-1"
    assert item.source_url == "https://example.com/post-1"
    assert item.raw_text == "Mapped body"
    assert item.raw_author.name == "Bob"
    assert item.raw_author.handle == "bob"
    assert item.raw_timestamps.published_at == "2026-04-24T10:00:00Z"


def test_opencli_connector_respects_cursor(monkeypatch):
    def fake_run(command, **kwargs):
        return _completed(
            json.dumps(
                {
                    "items": [
                        {"id": "old", "title": "Old"},
                        {"id": "new", "title": "New"},
                    ]
                }
            )
        )

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(connector_id="opencli_hn", command=["hackernews", "top"])

    result = connector.fetch_new_items(since_cursor="old")

    assert [item.raw_item_id for item in result.items] == ["new"]
    assert result.next_cursor == "new"


def test_opencli_connector_contract_reports_order_counts_cursor_and_empty_semantics(monkeypatch):
    payload = json.dumps(
        [
            {"id": "newest", "title": "Newest"},
            {"id": "seen", "title": "Seen"},
            {"id": "oldest", "title": "Oldest"},
        ]
    )
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", lambda *_args, **_kwargs: _completed(payload))
    connector = OpenCLIConnector(
        connector_id="opencli_contract",
        command=["twitter", "tweets", "contract"],
        source_platform="x",
        source_type="social_post",
        result_order="newest_first",
    )

    result = connector.fetch_new_items(since_cursor="seen")

    assert [item.raw_item_id for item in result.items] == ["newest"]
    assert result.next_cursor == "newest"
    assert result.visible_count == 3
    assert result.dedup_skipped_count == 2

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", lambda *_args, **_kwargs: _completed("[]"))
    empty = connector.fetch_new_items(since_cursor="newest")

    assert empty.items == []
    assert empty.next_cursor == "newest"
    assert empty.visible_count == 0
    assert empty.dedup_skipped_count == 0


def test_opencli_connector_contract_normalizes_timeout_to_runtime_error(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="opencli", timeout=60)

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", timeout)
    connector = OpenCLIConnector(connector_id="opencli_contract", command=["hackernews", "top"])

    with pytest.raises(RuntimeError, match="timed out after 60s"):
        connector.fetch_new_items(since_cursor=None)


def test_xiaohongshu_retries_one_timeout_inside_the_same_tab_lease(monkeypatch):
    calls = 0
    lease_events: list[str] = []
    lease_kwargs: list[dict[str, object]] = []

    class FakeLease:
        def __enter__(self):
            lease_events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            lease_events.append("exit")

    def first_timeout_then_loaded_page(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(cmd="opencli", timeout=60)
        return _completed(
            json.dumps(
                [
                    {
                        "id": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "Loaded after navigation",
                        "url": "https://www.xiaohongshu.com/explore/note-1",
                    }
                ]
            )
        )

    monkeypatch.setattr(
        "backend.infrastructure.connectors.opencli.opencli_tab_lease",
        lambda *_args, **kwargs: lease_kwargs.append(kwargs) or FakeLease(),
    )
    monkeypatch.setattr(
        "backend.infrastructure.connectors.opencli.subprocess.run",
        first_timeout_then_loaded_page,
    )
    connector = OpenCLIConnector(
        connector_id="opencli_xiaohongshu",
        command=["xiaohongshu", "search", "AI 工具付费", "--limit", "1"],
        source_platform="xiaohongshu",
        source_type="social_post",
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert calls == 2
    assert lease_events == ["enter", "exit"]
    assert lease_kwargs == [{"executable": "opencli", "close_tabs": False}]
    assert result.items[0].raw_item_id


def test_opencli_connector_raises_for_failed_command(monkeypatch):
    def fake_run(command, **kwargs):
        return _completed("", returncode=77, stderr="auth required")

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(connector_id="opencli_x", command=["twitter", "timeline"])

    with pytest.raises(RuntimeError, match="exit code 77"):
        connector.fetch_new_items(since_cursor=None)


def test_opencli_connector_keeps_only_redacted_summary_for_failed_command(monkeypatch):
    def fake_run(command, **kwargs):
        return _completed(
            "ok: false\n"
            "error:\n"
            "  code: AUTH_REQUIRED\n"
            "  message: Reddit /api/me.json returned HTTP 403\n"
            "  url: https://example.com/path?next=private&plain=value\n",
            returncode=77,
            stderr=(
                "node warning\n"
                "Authorization: Bearer abcdefghijklmnop\n"
                "Cookie: sessionid=raw-session\n"
                'email=user@example.com phone=555-0199 "token":"raw-token"\n'
            ),
        )

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_reddit",
        command=["reddit", "whoami"],
        source_platform="reddit",
        source_type="account",
    )

    with pytest.raises(RuntimeError) as excinfo:
        connector.fetch_new_items(since_cursor=None)

    error = excinfo.value
    message = str(error)
    summary = error.stderr_summary
    assert message == "OpenCLI command failed with exit code 77"
    assert "AUTH_REQUIRED" not in message
    assert "Reddit /api/me.json returned HTTP 403" in summary
    assert len(summary) <= 800
    assert "raw-session" not in summary
    assert "raw-token" not in summary
    assert "user@example.com" not in summary
    assert "555-0199" not in summary
    assert "next=[REDACTED]" in summary
    assert "plain=[REDACTED]" in summary


def test_opencli_connector_retries_stale_page_identity_once(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return _completed(
                "ok: false\n"
                "error:\n"
                "  code: COMMAND_EXEC\n"
                "  message: 'Page not found: abc — stale page identity'\n",
                returncode=1,
            )
        return _completed(json.dumps([{"id": "r1", "title": "Recovered", "url": "https://reddit.com/r/x/r1"}]))

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)
    connector = OpenCLIConnector(
        connector_id="opencli_reddit",
        command=["reddit", "subreddit", "LocalLLaMA", "--limit", "1"],
        source_platform="reddit",
        source_type="social_post",
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert len(calls) == 2
    assert result.items[0].raw_item_id == "r1"


def test_load_source_configs_includes_opencli_sources(tmp_path):
    from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
    from backend.infrastructure.storage.repositories import SourceRepository

    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source({
        "source_id": "opencli_hn",
        "platform": "x",
        "source_type": "creator",
        "identifier": "hackernews_top",
        "connector_id": "opencli_hn",
        "cadence_hours": 1.0,
        "enabled": True,
        "connector_status": "supported",
    })

    sources = load_source_configs(tmp_path)

    assert sources == [
        {
            "source_id": "opencli_hn",
            "connector_id": "opencli_hn",
            "enabled": True,
            "requires_browser_session": True,
            "type": "opencli",
            "executable": "opencli",
            "command": ["twitter", "tweets", "hackernews_top", "--limit", "50"],
            "source_platform": "x",
            "source_type": "social_post",
            "result_order": "newest_first",
            "field_mapping": {
                "id": "id",
                "title": "text",
                "url": "url",
                "text": "text",
                "author_name": "author.name",
                "author_handle": "author.username",
                "published_at": "created_at",
            },
            "metadata_mapping": {
                "views": "views",
                "likes": "likes",
                "replies": "replies",
                "retweets": "retweets",
                "bookmarks": "bookmarks",
                "quotes": "quotes",
            },
        }
    ]


def test_fetch_supports_opencli_source_and_persists_raw_items(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        return _completed(
            json.dumps(
                {
                    "items": [
                        {
                            "id": "hn-1",
                            "title": "OpenCLI ships useful data",
                            "url": "https://news.ycombinator.com/item?id=1",
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)

    from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
    from backend.infrastructure.storage.repositories import SourceCursorRepository, SourceRepository

    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source({
        "source_id": "opencli_hn",
        "platform": "x",
        "source_type": "creator",
        "identifier": "hackernews_top",
        "connector_id": "opencli_hn",
        "cadence_hours": 1.0,
        "enabled": True,
        "connector_status": "supported",
    })

    result = fetch(base_dir=tmp_path)

    assert result["fetched_count"] == 1
    raw_output = Path(result["exports"]["raw_outputs"][0])
    raw_payload = raw_output.read_text(encoding="utf-8")
    assert '"connector_id":"opencli_hn"' in raw_payload
    assert '"source_platform":"x"' in raw_payload
    assert '"opencli_record"' in raw_payload
    cursor = SourceCursorRepository(tmp_path).get("opencli_hn")
    assert cursor is not None
    assert cursor["cursor_value"] == "hn-1"


def test_main_fetch_command_supports_opencli_source(tmp_path, monkeypatch, capsys):
    def fake_run(command, **kwargs):
        return _completed(json.dumps([{"id": "item-1", "title": "CLI fetch"}]))

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)

    from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
    from backend.infrastructure.storage.repositories import SourceRepository

    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source({
        "source_id": "opencli_test",
        "platform": "x",
        "source_type": "creator",
        "identifier": "demo",
        "connector_id": "opencli_test",
        "cadence_hours": 1.0,
        "enabled": True,
        "connector_status": "supported",
    })

    exit_code = fetch_main(["--base-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"fetched_count": 1' in captured.out


def test_youtube_channel_records_parses_field_value_rows():
    from backend.infrastructure.connectors.opencli import _youtube_channel_records

    rows = [
        {"field": "name", "value": "OpenAI"},
        {"field": "handle", "value": "@openai"},
        {"field": "---", "value": ""},
        {"field": "GPT-5 Announcement", "value": "15:30 | 500K views | 3 days ago | https://www.youtube.com/watch?v=abc123"},
        {"field": "Sora Update", "value": "8:20 | 200K views | 1 week ago | https://www.youtube.com/watch?v=def456"},
    ]

    result = _youtube_channel_records(rows)
    assert len(result) == 2
    assert result[0]["title"] == "GPT-5 Announcement"
    assert result[0]["url"] == "https://www.youtube.com/watch?v=abc123"
    assert result[0]["channel"] == "OpenAI"
    assert result[1]["title"] == "Sora Update"
