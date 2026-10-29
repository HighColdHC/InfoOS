"""Historical-card delivery policy at the durable enqueue boundary."""
from __future__ import annotations

import json
import sqlite3

import pytest

from backend.application.tasks.card_delivery_transitions import enqueue_webhooks
from backend.application.tasks.finalization import TaskFinalizationQueue
from backend.application.tasks.handlers.webhook_deliver import _payload
from backend.application.tasks.types import Task, TaskType
from backend.application.usecases.api_tokens import create_token
from backend.domain.backfill import is_backfill
from backend.infrastructure.storage.database import default_database_path


@pytest.mark.parametrize(
    ("published", "seen", "initial", "expected"),
    [
        (None, "2026-07-20T00:00:00Z", True, (True, "initial_fetch")),
        ("2026-07-17T23:59:59Z", "2026-07-20T00:00:00Z", False,
         (True, "published_over_48h_before_discovery")),
        ("2026-07-18T00:00:00Z", "2026-07-20T00:00:00Z", False, (False, None)),
        ("2026-07-20T01:00:01Z", "2026-07-20T00:00:00Z", False,
         (False, "future_published_at_ignored")),
        (None, "2026-07-20T00:00:00Z", False, (False, None)),
    ],
)
def test_backfill_policy(published, seen, initial, expected):
    assert is_backfill(published, seen, initial) == expected


@pytest.mark.parametrize("initial,published,capture_id,expected_backfill", [
    (True, "2026-07-20T00:00:00Z", None, True),
    (False, "2026-07-17T00:00:00Z", None, True),
    (False, "2026-07-17T00:00:00Z", "capture-1", False),
    (False, "2026-07-20T00:00:00Z", None, False),
    (False, "2026-07-20T02:00:00Z", None, False),
])
def test_enqueue_respects_token_backfill_setting(tmp_path, initial, published, capture_id, expected_backfill):
    ordinary = create_token(tmp_path, name="ordinary", scopes=["cards:read"],
                            webhook_enabled=True, webhook_url="http://127.0.0.1:27124/hook")["token"]
    historical = create_token(tmp_path, name="history", scopes=["cards:read"],
                              webhook_enabled=True, webhook_include_backfill=True,
                              webhook_url="http://127.0.0.1:27125/hook")["token"]
    conn = sqlite3.connect(default_database_path(tmp_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """insert into raw_items (connector_id, raw_item_id, source_platform, source_type,
               source_url, captured_at, first_seen_at, last_seen_at)
               values ('rss', 'item', 'rss', 'feed', 'https://example.com/item', ?, ?, ?)""",
            ("2026-07-20T00:00:00Z",) * 3,
        )
        card = {"card_id": "card", "source_id": None, "status": "active", "content_hash": "hash", "version": 1,
                "raw_connector_id": "rss", "raw_item_id": "item", "published_at": published,
                "updated_at": "2026-07-20T00:01:00Z"}
        parent = Task(task_id="finalize", task_type=TaskType.CARD_FINALIZE,
                      target_kind="card", target_id="card",
                      artifacts_summary={"initial_fetch": initial, "capture_id": capture_id})
        ids = enqueue_webhooks(TaskFinalizationQueue(conn, now="2026-07-20T00:01:00Z"),
                               parent, card=card)
        rows = [json.loads(row[0]) for row in conn.execute(
            "select artifacts_summary from tasks where task_type = 'webhook_deliver'")]
        assert len(ids) == (1 if expected_backfill else 2)
        assert {row["token_id"] for row in rows} == (
            {historical["token_id"]} if expected_backfill
            else {ordinary["token_id"], historical["token_id"]}
        )
        if expected_backfill:
            task = Task(task_id="delivery", task_type=TaskType.WEBHOOK_DELIVER,
                        target_kind="card", target_id="card", artifacts_summary=rows[0])
            assert _payload(task)["backfill"] is True
        else:
            included = next(row for row in rows if row["token_id"] == historical["token_id"])
            ordinary_row = next(row for row in rows if row["token_id"] == ordinary["token_id"])
            assert included["backfill"] is False
            assert "backfill" not in ordinary_row
            task = Task(task_id="delivery", task_type=TaskType.WEBHOOK_DELIVER,
                        target_kind="card", target_id="card", artifacts_summary=included)
            assert _payload(task)["backfill"] is False
    finally:
        conn.close()
