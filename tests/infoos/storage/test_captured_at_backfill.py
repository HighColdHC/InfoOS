"""v0010 replaces legacy publisher dates in captured_at with first_seen_at."""
import sqlite3

import pytest

from backend.domain.timestamps import captured_at_needs_repair
from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database


@pytest.mark.parametrize(("captured", "published", "expected"), [
    ("Tue Jul 21 20:13:46 +0000 2026", None, True),
    ("1784729776", None, True),
    ("pirms 5 dienām", None, True),
    ("https://www.youtube.com/watch?v=x", None, True),
    ("2026-07-21", None, True),
    ("2026-07-21T20:13:46+08:00", None, True),
    ("2026-07-22T13:00:00Z", "2026-07-22T13:00:00Z", True),
    ("2026-07-22T13:00:00Z", "Wed, 22 Jul 2026 13:00:00 GMT", True),
    ("2026-07-22T13:00:00Z", "2026-07-21T08:00:00Z", False),
    ("2026-07-22T13:00:00.123456Z", None, False),
])
def test_needs_repair(captured, published, expected):
    assert captured_at_needs_repair(captured, published) is expected


# (raw_item_id, captured_at, published_at, first_seen_at); first_seen None = card without raw item.
ROWS = [
    ("x-1", "Tue Jul 21 20:13:46 +0000 2026", "2026-07-21T20:13:46Z", "2026-07-22T01:00:00Z"),
    ("reddit-1", "1784729776", None, "2026-07-22T02:00:00Z"),
    ("rss-copy", "2026-07-22T13:00:00Z", "2026-07-22T13:00:00Z", "2026-07-22T14:00:00Z"),
    ("good", "2026-07-22T15:00:00.500000Z", "2026-07-01T00:00:00Z", "2026-07-22T15:00:00Z"),
    ("same-instant", "2026-07-22T16:00:00Z", "2026-07-22T16:00:00Z", "2026-07-22T16:00:00Z"),
    ("orphan", "pirms 5 dienām", None, None),
]


def _v9_db(tmp_path):
    db = tmp_path / "infoos.sqlite3"

    def stop(event):
        if event["migration"] == 10:
            raise RuntimeError("pre-v10")

    with pytest.raises(RuntimeError, match="pre-v10"):
        migrate_database(db, progress_callback=stop)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 9
        for raw_id, captured, published, first_seen in ROWS:
            if first_seen:
                conn.execute(
                    """insert into raw_items (connector_id, raw_item_id, source_platform, source_type,
                       source_url, title, captured_at, published_at, raw_payload, first_seen_at, last_seen_at)
                       values ('c', ?, 'x', 'post', '', '', ?, ?, '{}', ?, ?)""",
                    (raw_id, captured, published, first_seen, first_seen))
            conn.execute(
                """insert into cards (card_id, version, updated_at, status, completeness_status,
                   missing_reasons, raw_connector_id, raw_item_id, source_platform, source_type,
                   source_url, source_author, title, content, captured_at, published_at, processed_at,
                   content_hash)
                   values (?, 3, '', 'active', 'complete', '[]', 'c', ?, 'x', 'post', '', 'a', 't', 'b',
                           ?, ?, '2026-09-30T02:00:00Z', 'hash')""",
                (f"card-{raw_id}", raw_id, captured, published))
    return db


def _captured(db):
    with sqlite3.connect(db) as conn:
        cards = dict(conn.execute("select raw_item_id, captured_at from cards"))
        raws = dict(conn.execute("select raw_item_id, captured_at from raw_items"))
        assert conn.execute("select distinct version, content_hash from cards").fetchall() == [(3, "hash")]
    return cards, raws


def test_backfill_interrupted_resumes_and_repeats_cleanly(tmp_path):
    db = _v9_db(tmp_path)
    seen = []

    def stop_after_first_batch(event):
        seen.append(event)
        if event.get("phase") == "cards" and len(seen) == 1:
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        migrate_database(db, batch_size=2, progress_callback=stop_after_first_batch)
    assert _captured(db)[0]["x-1"] == ROWS[0][1]  # the failed batch rolled back

    report = migrate_database(db, batch_size=2)
    assert (report.before_version, report.after_version) == (9, LATEST_VERSION)
    assert sum(e.get("repaired", 0) for e in report.batches) == 6
    cards, raws = _captured(db)
    expected = {"x-1": "2026-07-22T01:00:00Z", "reddit-1": "2026-07-22T02:00:00Z",
                "rss-copy": "2026-07-22T14:00:00Z", "good": "2026-07-22T15:00:00.500000Z",
                "same-instant": "2026-07-22T16:00:00Z"}
    assert raws == expected
    assert cards == {**expected, "orphan": "pirms 5 dienām"}
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*) from settings where key like '_migration.v0010.%'").fetchone() == (0,)
    assert migrate_database(db).before_version == LATEST_VERSION
    assert _captured(db) == (cards, raws)
