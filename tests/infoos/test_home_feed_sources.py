from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.setup.state import AccountState, AccountStatus
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskType
from backend.application.workflows.acquisition import fetch_source
from backend.application.workflows.acquisition_failures import AcquisitionFailureError
from backend.application.workflows.fetch_support import build_connector
from backend.domain.engagement_metrics import X_ENGAGEMENT_METADATA_MAPPING
from backend.domain.sources.configuration import (
    database_source_to_connector_config,
    validate_source_payload,
)
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import SourceRepository


HOME_FEED_CASES = (
    (
        "x", "for-you", 1,
        ["twitter", "timeline", "--type", "for-you", "--limit", "7"],
        "social_post", "snapshot",
        {
            "id": "id", "title": "text", "url": "url", "text": "text",
            "author_name": "author", "author_handle": "author",
            "published_at": "created_at",
        },
        dict(X_ENGAGEMENT_METADATA_MAPPING),
    ),
    (
        "x", "following", 1,
        ["twitter", "timeline", "--type", "following", "--limit", "7"],
        "social_post", "newest_first",
        {
            "id": "id", "title": "text", "url": "url", "text": "text",
            "author_name": "author", "author_handle": "author",
            "published_at": "created_at",
        },
        dict(X_ENGAGEMENT_METADATA_MAPPING),
    ),
    (
        "youtube", "recommended", 6,
        ["youtube", "feed", "--limit", "7"],
        "video", "snapshot",
        {
            "id": "url", "title": "title", "url": "url", "text": "title",
            "author_name": "channel", "published_at": "published",
        },
        {"rank": "rank", "views": "views", "duration": "duration", "video_id": "video_id"},
    ),
    (
        "reddit", "best", 1,
        ["reddit", "home", "--limit", "7"],
        "social_post", "snapshot",
        {
            "id": "postId", "title": "title", "url": "url", "text": "title",
            "author_name": "author", "author_handle": "author",
            "published_at": "created_utc",
        },
        {
            "subreddit": "subreddit", "score": "score", "comments": "comments",
            "post_hint": "post_hint", "url_overridden_by_dest": "url_overridden_by_dest",
            "preview_image_url": "preview_image_url", "gallery_urls": "gallery_urls",
        },
    ),
    (
        "xiaohongshu", "recommended", 5,
        ["xiaohongshu", "feed", "--limit", "7"],
        "social_post", "snapshot",
        {
            "id": "id", "title": "title", "url": "url", "text": "content",
            "author_name": "author", "published_at": "published_at",
        },
        {"likes": "likes", "type": "type"},
    ),
)


@pytest.mark.parametrize(
    (
        "platform", "identifier", "cadence_hours", "command", "raw_item_type",
        "result_order", "field_mapping", "metadata_mapping",
    ),
    HOME_FEED_CASES,
)
def test_home_feed_payload_and_connector_projection(
    platform,
    identifier,
    cadence_hours,
    command,
    raw_item_type,
    result_order,
    field_mapping,
    metadata_mapping,
):
    source = validate_source_payload(
        {
            "source_id": f"{platform}_{identifier}",
            "platform": platform,
            "source_type": "home_feed",
            "identifier": identifier,
            "cadence_hours": cadence_hours,
            "enabled": True,
            "max_items_per_run": 7,
        }
    )

    connector = database_source_to_connector_config(source)

    assert connector["command"] == command
    assert connector["source_platform"] == platform
    assert connector["source_type"] == raw_item_type
    assert connector["requires_browser_session"] is True
    assert connector["result_order"] == result_order
    assert connector["field_mapping"] == field_mapping
    assert connector["metadata_mapping"] == metadata_mapping
    if platform == "xiaohongshu":
        assert connector["detail_command_template"] == ["xiaohongshu", "note", "{url}"]


def test_home_feed_payload_rejects_unknown_identifier():
    with pytest.raises(
        ValueError,
        match="x home_feed identifier must be one of: following, for-you",
    ):
        validate_source_payload(
            {
                "platform": "x",
                "source_type": "home_feed",
                "identifier": "custom-command",
                "cadence_hours": 1,
            }
        )


def test_home_feed_capabilities_are_limited_to_five_platforms(tmp_path):
    response = TestClient(create_app(tmp_path)).get("/api/platform-capabilities")

    assert response.status_code == 200
    feeds = {
        platform["platform"]: source
        for platform in response.json()["platforms"]
        for source in platform["source_types"]
        if source["source_type"] == "home_feed"
    }
    assert set(feeds) == {"x", "youtube", "reddit", "xiaohongshu", "douyin"}
    assert feeds["x"]["identifier_options"] == [
        {"value": "for-you", "label": "为你推荐"},
        {"value": "following", "label": "正在关注"},
    ]
    assert feeds["youtube"]["default_cadence_hours"] == 6
    assert feeds["reddit"]["identifier_options"] == [{"value": "best", "label": "最佳"}]
    assert feeds["xiaohongshu"]["default_cadence_hours"] == 5
    assert feeds["douyin"]["default_max_items"] == 40
    assert feeds["douyin"]["default_cadence_hours"] == 1
    assert all(feed["default_max_items"] == 20 for name, feed in feeds.items() if name != "douyin")
    assert all(feed["requires_login"] == "required" for feed in feeds.values())


def test_home_feed_create_persists_and_enqueues_initial_fetch(tmp_path):
    payload = {
        "source_id": "x_for_you",
        "platform": "x",
        "source_type": "home_feed",
        "identifier": "for-you",
        "display_name": "X 为你推荐",
        "cadence_hours": 5,
        "enabled": True,
        "max_items_per_run": 13,
    }

    response = TestClient(create_app(tmp_path)).post(
        "/api/configurable-sources",
        json=payload,
    )

    assert response.status_code == 201
    stored = SourceRepository(tmp_path).get_source("x_for_you")
    assert stored["cadence_hours"] == 5
    assert stored["raw_metadata"]["max_items_per_run"] == 13
    tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="source",
        target_id="x_for_you",
    )
    assert [task.task_type for task in tasks] == [TaskType.FETCH]
    assert tasks[0].artifacts_summary["initial_fetch"] is True


RAW_ITEM_CASES = (
    (
        "x", "for-you",
        {
            "id": "tweet-1", "text": "X personalized item",
            "url": "https://x.com/example/status/1", "author": "example",
            "created_at": "2026-07-26T08:00:00Z", "likes": 12,
        },
        None,
        (
            "tweet-1", "https://x.com/example/status/1", "X personalized item",
            "example", "2026-07-26T08:00:00Z", {"likes": 12},
        ),
    ),
    (
        "youtube", "recommended",
        {
            "title": "YouTube recommendation",
            "url": "https://www.youtube.com/watch?v=video-1",
            "channel": "Example Channel", "published": "2026-07-25T08:00:00Z",
            "rank": 1, "views": "1234", "duration": "10:00", "video_id": "video-1",
        },
        None,
        (
            "https://www.youtube.com/watch?v=video-1",
            "https://www.youtube.com/watch?v=video-1",
            "YouTube recommendation", "Example Channel", "2026-07-25T08:00:00Z",
            {"rank": 1, "views": "1234", "duration": "10:00", "video_id": "video-1"},
        ),
    ),
    (
        "reddit", "best",
        {
            "postId": "reddit-1", "title": "Reddit personalized post",
            "url": "https://www.reddit.com/r/example/comments/reddit-1",
            "author": "redditor", "created_utc": "2026-07-24T08:00:00Z",
            "subreddit": "example", "score": 42, "comments": 7,
        },
        None,
        (
            "reddit-1", "https://www.reddit.com/r/example/comments/reddit-1",
            "Reddit personalized post", "redditor", "2026-07-24T08:00:00Z",
            {"subreddit": "example", "score": 42, "comments": 7},
        ),
    ),
    (
        "xiaohongshu", "recommended",
        {
            "id": "xhs-1", "title": "小红书推荐标题",
            "url": "https://www.xiaohongshu.com/explore/xhs-1",
            "author": "小红书作者", "published_at": "2026-07-23T08:00:00Z",
            "likes": 88, "type": "normal",
        },
        {"content": "小红书详情正文"},
        (
            "xhs-1", "https://www.xiaohongshu.com/explore/xhs-1",
            "小红书详情正文", "小红书作者", "2026-07-23T08:00:00Z",
            {"likes": 88, "type": "normal"},
        ),
    ),
)


@pytest.mark.parametrize(
    ("platform", "identifier", "discovery", "detail", "expected"),
    RAW_ITEM_CASES,
)
def test_home_feed_connector_emits_representative_raw_items(
    tmp_path,
    monkeypatch,
    platform,
    identifier,
    discovery,
    detail,
    expected,
):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        payload = detail if detail is not None and "note" in command else [discovery]
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(
        "backend.infrastructure.connectors.opencli.subprocess.run",
        fake_run,
    )
    source = validate_source_payload(
        {
            "source_id": f"{platform}_home_raw",
            "platform": platform,
            "source_type": "home_feed",
            "identifier": identifier,
            "cadence_hours": 5 if platform == "xiaohongshu" else 1,
            "enabled": True,
            "max_items_per_run": 1,
        }
    )
    connector = build_connector(tmp_path, database_source_to_connector_config(source))

    result = connector.fetch_new_items(since_cursor=None)

    item = result.items[0]
    expected_id, url, text, author, published_at, metadata = expected
    assert item.raw_item_id == expected_id
    assert item.source_url == url
    assert item.raw_text == text
    assert item.raw_author.name == author
    assert item.raw_timestamps.published_at == published_at
    for key, value in metadata.items():
        assert item.raw_metadata[key] == value
    if detail is not None:
        assert len(calls) == 2
        assert item.raw_metadata["opencli_record"]["content"] == detail["content"]


def test_personalized_snapshot_cursor_keeps_reordered_insertions(tmp_path, monkeypatch):
    payloads = iter(
        (
            [
                {"id": "A", "text": "A", "url": "https://x.com/a/status/A"},
                {"id": "B", "text": "B", "url": "https://x.com/a/status/B"},
            ],
            [
                {"id": "A", "text": "A", "url": "https://x.com/a/status/A"},
                {"id": "C", "text": "C", "url": "https://x.com/a/status/C"},
                {"id": "B", "text": "B", "url": "https://x.com/a/status/B"},
            ],
        )
    )
    monkeypatch.setattr(
        "backend.infrastructure.connectors.opencli.subprocess.run",
        lambda _command, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(next(payloads)),
            stderr="",
        ),
    )
    source = validate_source_payload(
        {
            "source_id": "x_for_you_snapshot",
            "platform": "x",
            "source_type": "home_feed",
            "identifier": "for-you",
            "cadence_hours": 1,
            "enabled": True,
            "max_items_per_run": 3,
        }
    )
    connector = build_connector(tmp_path, database_source_to_connector_config(source))

    first = connector.fetch_new_items(since_cursor=None)
    second = connector.fetch_new_items(since_cursor=first.next_cursor)

    assert [item.raw_item_id for item in first.items] == ["A", "B"]
    assert [item.raw_item_id for item in second.items] == ["C"]
    assert second.visible_count == 3
    assert second.dedup_skipped_count == 2
    assert second.next_cursor == 'snapshot:["A","C","B"]'


class _EmptyHomeFeedConnector:
    connector_id = "youtube_home_login"

    def fetch_new_items(self, since_cursor):
        return FetchResult(items=[], next_cursor=since_cursor, visible_count=0)


def _create_and_claim_youtube_home_feed(base_dir):
    response = TestClient(create_app(base_dir)).post(
        "/api/configurable-sources",
        json={
            "source_id": "youtube_home_login",
            "platform": "youtube",
            "source_type": "home_feed",
            "identifier": "recommended",
            "display_name": "YouTube 推荐",
            "cadence_hours": 6,
            "enabled": True,
            "max_items_per_run": 20,
        },
    )
    assert response.status_code == 201
    repo = TaskRepository.for_base_dir(base_dir)
    task = repo.list_by_target(
        target_kind="source",
        target_id="youtube_home_login",
    )[0]
    return repo.claim_task_ids(
        [task.task_id],
        worker_id="home-feed-test-worker",
        now="2026-07-26T08:00:00Z",
        lease_expires_at="2026-07-26T08:05:00Z",
        task_types=[TaskType.FETCH],
    )[0]


def test_home_feed_initial_fetch_fails_closed_without_verified_login(
    tmp_path,
    monkeypatch,
):
    task = _create_and_claim_youtube_home_feed(tmp_path)
    monkeypatch.setattr(
        "backend.application.setup.verify.probe_platform",
        lambda _platform: AccountState(
            status=AccountStatus.NEEDS_LOGIN,
            error="not logged in",
        ),
    )
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda _base_dir, _source: pytest.fail("connector must not run"),
    )

    with pytest.raises(AcquisitionFailureError) as raised:
        fetch_source(tmp_path, source_id="youtube_home_login", task=task)

    assert raised.value.failure.diagnostic["category"] == "auth_required"
    assert raised.value.failure.retryable is False
    assert (
        SourceRepository(tmp_path).get_source("youtube_home_login")["connector_status"]
        == "implemented_uncalibrated"
    )


def test_home_feed_initial_fetch_promotes_after_verified_login(tmp_path, monkeypatch):
    task = _create_and_claim_youtube_home_feed(tmp_path)
    monkeypatch.setattr(
        "backend.application.setup.verify.probe_platform",
        lambda _platform: AccountState(status=AccountStatus.VERIFIED),
    )
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda _base_dir, _source: _EmptyHomeFeedConnector(),
    )

    fetch_source(tmp_path, source_id="youtube_home_login", task=task)

    assert (
        SourceRepository(tmp_path).get_source("youtube_home_login")["connector_status"]
        == "supported"
    )
