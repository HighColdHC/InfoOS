from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from backend.application.pipeline.information_card_builder import build_information_card
from backend.application.pipeline.normalize import normalize_raw_item
from backend.domain.models.raw_item import RawItem, RawTimestamps
from backend.infrastructure.connectors.feed.rss import RSSConnector
from backend.infrastructure.connectors.opencli import OpenCLIConnector
from backend.infrastructure.storage.repositories import CardRepository, RawItemRepository, SourceRepository
from backend.application.tasks.handlers.acquisition import handle_card_build
from backend.application.tasks.types import Task, TaskType


@pytest.mark.parametrize("published", ["Fri Aug 07", "https://ww", "pirms 1 ga"])
def test_opencli_keeps_unparseable_platform_time_out_of_capture(published: str) -> None:
    connector = OpenCLIConnector(connector_id="reddit_test", command=["reddit", "posts"])
    raw = connector._to_raw_item({"id": "post-1", "url": "https://example.com/1", "date": published})

    assert raw.captured_at.endswith("Z") and raw.captured_at.startswith("20")
    assert raw.captured_at != published
    assert raw.raw_metadata["opencli_record"]["date"] == published

    card = build_information_card(normalize_raw_item(raw), raw)
    assert card.captured_at == raw.captured_at
    assert card.published_at is None


def test_rss_keeps_bad_pubdate_as_raw_evidence(tmp_path: Path) -> None:
    connector = RSSConnector("rss_test", "https://example.com/feed", tmp_path)
    item = ET.fromstring("<item><guid>one</guid><title>Title</title><pubDate>Fri Aug 07</pubDate></item>")

    raw = connector._to_raw_item(item, "one")

    assert raw.captured_at.endswith("Z")
    assert raw.raw_timestamps.published_at is None
    assert raw.raw_metadata["published_at_original"] == "Fri Aug 07"


def test_card_builder_rejects_legacy_bad_capture() -> None:
    raw = RawItem(
        raw_item_id="one", connector_id="reddit_test", source_platform="reddit",
        source_type="post", source_url="https://example.com/1",
        captured_at="pirms 1 ga", raw_timestamps=RawTimestamps(published_at="pirms 1 ga"),
    )
    card = build_information_card(normalize_raw_item(raw), raw, processed_at="2026-08-07T10:00:00Z")
    assert card.captured_at == "2026-08-07T10:00:00Z"


def test_legacy_raw_card_build_uses_first_seen_capture(tmp_path: Path) -> None:
    SourceRepository(tmp_path).upsert_source({
        "source_id": "reddit_test", "platform": "reddit", "source_type": "post",
        "identifier": "reddit_test", "display_name": "Reddit test",
        "cadence_hours": 1, "enabled": True, "connector_status": "supported",
        "connector_id": "reddit_test",
    })
    raw = RawItem(
        raw_item_id="one", connector_id="reddit_test", source_platform="reddit",
        source_type="post", source_url="https://example.com/one",
        captured_at="Fri Aug 07", raw_metadata={"source_id": "reddit_test"},
    )
    RawItemRepository(tmp_path).upsert_raw_items([raw])
    stored = RawItemRepository(tmp_path).get_for_connector("reddit_test", "one")
    assert stored["captured_at"].endswith("Z")
    assert stored["raw_payload"]["captured_at"] == stored["captured_at"]

    db_path = RawItemRepository(tmp_path).db_path
    with sqlite3.connect(db_path) as conn:
        legacy_payload = {**stored["raw_payload"], "captured_at": "Fri Aug 07"}
        conn.execute(
            "update raw_items set captured_at = ?, raw_payload = ?, first_seen_at = ? where connector_id = ? and raw_item_id = ?",
            ("Fri Aug 07", json.dumps(legacy_payload), "2026-08-07T10:00:00Z", "reddit_test", "one"),
        )
    result = handle_card_build(Task(
        task_id="card_build_one", task_type=TaskType.CARD_BUILD,
        target_kind="raw_item", target_id="one",
    ), tmp_path)
    assert result["status"] == "success"
    assert CardRepository(tmp_path).get_card(result["artifacts"]["card_id"])["captured_at"] == "2026-08-07T10:00:00Z"


def test_preview_uses_raw_then_source_run_and_lists_unresolved(tmp_path: Path) -> None:
    database = tmp_path / "preview.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            create table cards (card_id text, source_platform text, captured_at text, published_at text,
                                raw_connector_id text, raw_item_id text, source_run_id text);
            create table raw_items (connector_id text, raw_item_id text, source_platform text,
                                    captured_at text, published_at text, first_seen_at text, run_id text);
            create table run_history (run_id text, started_at text);
            insert into raw_items values ('x', 'one', 'reddit', '1784729776', '1784729776',
                                          '2026-08-07T10:00:00Z', 'run-1');
            insert into raw_items values ('r', 'five', 'rss', '2026-08-01T09:00:00Z', '2026-08-01T09:00:00Z',
                                          '2026-08-07T12:00:00Z', null);
            insert into run_history values ('run-1', '2026-08-06T08:00:00Z');
            insert into run_history values ('run-2', '2026-08-05T08:00:00Z');
            insert into cards values ('one', 'reddit', '1784729776', '1784729776', 'x', 'one', 'run-1');
            insert into cards values ('two', 'youtube', 'https://www.youtube.com/watch?v=a', null, null, null, 'run-2');
            insert into cards values ('three', 'youtube', 'pirms 1 gada', 'pirms 1 gada', null, null, null);
            insert into cards values ('four', 'rss', '2026-08-07T11:00:00Z', '2026-08-01T00:00:00Z', null, null, null);
            insert into cards values ('five', 'rss', '2026-08-01T09:00:00Z', '2026-08-01T09:00:00Z', 'r', 'five', null);
        """)
    script = Path(__file__).resolve().parents[2] / "scripts" / "preview-captured-at-backfill.py"
    result = subprocess.run([sys.executable, str(script), str(database)], capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)

    assert report["total_cards"] == 5
    assert report["needs_repair_cards"] == 4
    assert report["would_update_cards"] == 3
    assert report["reasons"] == {"non_canonical": 3, "publish_time_copy": 1}
    assert report["by_platform"] == {"reddit": 1, "rss": 1, "youtube": 2}
    samples = {sample["card_id"]: sample for sample in report["samples"]}
    assert samples["one"]["after"] == "2026-08-07T10:00:00Z"
    assert samples["two"]["after"] == "2026-08-05T08:00:00Z"
    assert samples["five"]["after"] == "2026-08-07T12:00:00Z"
    assert "four" not in samples
    assert report["unresolved"] == [{"card_id": "three", "platform": "youtube", "before": "pirms 1 gada"}]
    assert report["raw_items"] == {
        "needs_repair_raw_items": 2, "by_platform": {"reddit": 1, "rss": 1}, "unresolved_count": 0,
    }
    with sqlite3.connect(database) as conn:
        assert conn.execute("select captured_at from cards where card_id = 'one'").fetchone()[0] == "1784729776"


@pytest.mark.parametrize(
    ("candidates", "expected"),
    [
        (("2026-08-07T10:00:00+08:00",), "2026-08-07T02:00:00Z"),
        (("Tue Jul 21 20:13:46 +0000 2026", "2026-08-07T10:00:00Z"), "2026-08-07T10:00:00Z"),
        (("1784729776", None, "2026-08-07T10:00:00.5Z"), "2026-08-07T10:00:00.500000Z"),
        (("2026-07-21", "2026-08-07T10:00:00Z"), "2026-08-07T10:00:00Z"),
    ],
)
def test_capture_timestamp_accepts_only_aware_iso(candidates: tuple, expected: str) -> None:
    from backend.domain.timestamps import capture_timestamp

    assert capture_timestamp(*candidates) == expected


@pytest.mark.parametrize("legacy", ["pirms 5 dienām", "https://www.youtube.com/watch?v=a", ""])
def test_capture_timestamp_falls_back_to_now(legacy: str) -> None:
    from backend.domain.timestamps import capture_timestamp

    assert capture_timestamp(legacy).endswith("Z")


def test_rebuild_ignores_legacy_iso_publish_copy(tmp_path: Path) -> None:
    raw = RawItem(
        raw_item_id="one", connector_id="rss_test", source_platform="rss",
        source_type="feed", source_url="https://example.com/one",
        captured_at="2026-07-22T13:00:00Z",
        raw_timestamps=RawTimestamps(published_at="2026-07-22T13:00:00Z"),
    )
    card = build_information_card(normalize_raw_item(raw), raw, first_seen_at="2026-07-24T07:50:27Z")
    assert card.captured_at == "2026-07-24T07:50:27Z"
    assert card.published_at == "2026-07-22T13:00:00Z"


def test_youtube_channel_row_without_date_does_not_use_url_as_published() -> None:
    from backend.infrastructure.connectors.opencli import _youtube_channel_records

    rows = [{"field": "GPT-Live", "value": "3:35 | OpenAI | https://www.youtube.com/watch?v=demoYt00003"}]
    assert _youtube_channel_records(rows)[0]["published_at"] is None
    dated = [{"field": "GPT-5", "value": "15:30 | 500K views | 3 days ago | https://www.youtube.com/watch?v=abc"}]
    assert _youtube_channel_records(dated)[0]["published_at"] == "3 days ago"
