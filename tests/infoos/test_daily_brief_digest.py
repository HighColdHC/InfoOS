"""Daily digest formatting and idempotent outbox submission."""
from copy import deepcopy

import pytest

from backend.application.tasks import registry
from backend.application.tasks.types import Task, TaskType
from backend.application.usecases import daily_brief_digest, notifications
from backend.application.usecases.interest_profiles import push_profile
from backend.domain.intelligence.brief_digest import format_brief_digest
from backend.infrastructure.notifications import config
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository
from tests.infoos.test_daily_intelligence import PROFILE


def brief(items=None, weekly=None):
    return {"brief_date": "2026-10-26", "items": items or [],
            "counts": {"related": 2 if items else 0, "exploration": 1 if items else 0},
            "weekly_paused": weekly}


def item(number, group, ref):
    return {"number": number, "group": group, "attribution_ref": ref, "card_id": f"c{number}",
            "title": f"标题{number}", "platform": "rss", "summary": "摘要" * 40,
            "deep_link": f"https://infoos.test/r/card/c{number}"}


def test_format_grouped_items_with_number_summary_and_links():
    payload = brief([item(1, "related", "项目A"), item(2, "related", "项目B"),
                     item(3, "exploration", None)])
    title, body = format_brief_digest(payload, "https://infoos.test/r/brief/2026-10-26")
    assert title == "InfoOS 日报 10-26｜相关 2 · 新方向 1"
    assert body.index("项目A") < body.index("项目B") < body.index("新方向")
    for number in range(1, 4):
        assert f"{number}. 标题{number}（rss）\n" + "摘要" * 30 + "…\n" in body
        assert f"https://infoos.test/r/card/c{number}?from=feishu" in body
    assert body.endswith("日报全文：https://infoos.test/r/brief/2026-10-26?from=feishu")


def test_format_empty_brief():
    title, body = format_brief_digest(brief(), "/r/brief/2026-10-26")
    assert title.endswith("相关 0 · 新方向 0")
    assert body == "今日没有过线的相关条目\n日报全文：/r/brief/2026-10-26?from=feishu"


def test_format_monday_paused_line_and_landmark():
    weekly = {"count": 5, "top": {"title": "暂放条目", "deep_link": "/r/card/top"}}
    _, body = format_brief_digest(brief([item(1, "landmark", "暂放")], weekly), "/r/brief/2026-10-26")
    assert "格局变化\n1. 标题1（rss）" in body
    assert "本周暂放 5 条：暂放条目 /r/card/top?from=feishu" in body


def configured(monkeypatch):
    value = deepcopy(config.DEFAULTS)
    monkeypatch.setattr(daily_brief_digest, "load_config", lambda: value)
    monkeypatch.setattr(notifications, "load_config", lambda: value)
    monkeypatch.setattr(notifications, "readiness", lambda c: {"feishu": True, "email": True})
    monkeypatch.setattr(notifications, "utc_now_seconds_z", lambda: "2026-10-25T23:00:00Z")
    return value


def test_generated_and_frozen_enqueue_exactly_once(tmp_path, monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv("INFOOS_PUBLIC_BASE_URL", "https://infoos.test")
    push_profile(tmp_path, PROFILE, source="test")
    task = Task("brief", TaskType.DAILY_BRIEF_BUILD, "daily_brief", "2026-10-26")
    first = registry._handle_daily_brief(task, tmp_path)
    assert first["artifacts"]["status"] == "generated"
    second = registry._handle_daily_brief(task, tmp_path)
    assert second["artifacts"]["status"] == "frozen"
    ids = first["artifacts"]["digest"]["notification_ids"]
    assert len(ids) == 2
    assert second["artifacts"]["digest"]["notification_ids"] == ids
    repo = NotificationOutboxRepository(tmp_path)
    with repo.connect_existing() as conn:
        rows = conn.execute("""select kind, channel, dedupe_key, related_kind, related_id,
            next_attempt_at, expires_at, body from notification_outbox""").fetchall()
    assert len(rows) == 2
    for row in rows:
        assert row["kind"] == "daily_brief_digest"
        assert row["dedupe_key"] == f"daily-brief:2026-10-26:{row['channel']}"
        assert (row["related_kind"], row["related_id"]) == ("brief", "2026-10-26")
        assert row["next_attempt_at"] == "2026-10-26T00:00:00Z"
        assert row["expires_at"] == "2026-10-26T15:59:00Z"
        assert "https://infoos.test/r/brief/2026-10-26?from=feishu" in row["body"]


@pytest.mark.parametrize("status", ["rebuilt", "skipped"])
def test_other_build_outcomes_never_enqueue(tmp_path, monkeypatch, status):
    calls = []
    monkeypatch.setattr(registry, "build_daily_brief", lambda *a: {"status": status})
    monkeypatch.setattr(registry, "enqueue_daily_brief_digest", lambda *a: calls.append(a))
    registry._handle_daily_brief(Task("brief", TaskType.DAILY_BRIEF_BUILD, "daily_brief", "2026-10-26"), tmp_path)
    assert calls == []


def test_disabled_digest_does_not_enqueue(tmp_path, monkeypatch):
    value = configured(monkeypatch)
    value["content_push"]["daily_brief_digest"] = False
    assert daily_brief_digest.enqueue_daily_brief_digest(tmp_path, brief()) == []
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        assert conn.execute("select count(*) from notification_outbox").fetchone()[0] == 0
