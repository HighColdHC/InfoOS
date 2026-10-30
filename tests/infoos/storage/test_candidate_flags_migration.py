"""v24 backfill and triggers preserve the v23 raw JSON eligibility rules."""
import json
import sqlite3

import pytest

from backend.infrastructure.storage.migrations import runner, v0024_candidate_flags as flags
from backend.infrastructure.storage.migrations.v0001_baseline import SchemaDriftError


PAYLOADS = [
    ({}, (0, 0)),
    ({"prefilter": {"status": "matched"}}, (1, 0)),
    ({"prefilter": {"status": "unknown"}}, (1, 0)),
    ({"prefilter": {"status": "rejected"}}, (0, 0)),
    ({"raw_metadata": {"prefilter": {"status": "matched"}}}, (1, 0)),
    ({"prefilter": None, "raw_metadata": {"prefilter": {"status": "matched"}}}, (0, 0)),
    ({"prefilter": {"status": "rejected"}, "raw_metadata": {"prefilter": {"status": "matched"}}}, (0, 0)),
    ({"prefilter": {"status": 1}}, (0, 0)),
    ({"raw_metadata": {"tags": ["AI 风向标周报", 3, None, {"name": "风向标"}]}}, (0, 1)),
    ({"raw_metadata": {"tags": "风向标"}}, (0, 0)),
    ({"prefilter": {"status": "matched"}, "raw_metadata": {"tags": ["风向标"]}}, (1, 1)),
]


def insert_raw(conn, connector, raw_id, payload):
    conn.execute("""insert into raw_items
        (connector_id,raw_item_id,source_platform,source_type,source_url,captured_at,
         raw_payload,first_seen_at,last_seen_at)
        values (?,?,'rss','article','','2026-10-29T00:00:00Z',?,
                '2026-10-29T00:00:00Z','2026-10-29T00:00:00Z')""",
        (connector, raw_id, json.dumps(payload)))


def materialized_values(conn):
    return conn.execute("""select connector_id,raw_item_id,prefilter_candidate,wind_vane_tagged
        from raw_item_candidate_flags order by connector_id,raw_item_id""").fetchall()


def make_v23(path):
    def stop(event):
        if event["migration"] == 24:
            raise RuntimeError("stop at v23")

    with pytest.raises(RuntimeError, match="stop at v23"):
        runner.migrate_database(path, progress_callback=stop)
    with sqlite3.connect(path) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 23
        assert conn.execute("select name from sqlite_schema where name='raw_item_candidate_flags'").fetchone() is None


def test_v23_upgrade_backfills_flags(tmp_path):
    path = tmp_path / "flags.sqlite3"
    make_v23(path)
    with sqlite3.connect(path) as conn:
        for i, (payload, _) in enumerate(PAYLOADS):
            insert_raw(conn, 'connector', str(i), payload)
        before = conn.execute("select * from raw_items order by raw_item_id").fetchall()
    runner.migrate_database(path)
    with sqlite3.connect(path) as conn:
        actual = {row[1]: row[2:] for row in materialized_values(conn)}
        assert actual == {str(i): expected for i, (_, expected) in enumerate(PAYLOADS)}
        assert conn.execute("select * from raw_items order by raw_item_id").fetchall() == before


def test_insert_and_payload_update_synchronize_flags(tmp_path):
    path = tmp_path / 'trigger.sqlite3'
    runner.migrate_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute('pragma foreign_keys=on')
        for i, (payload, expected) in enumerate(PAYLOADS):
            raw_id = str(i)
            insert_raw(conn, 'insert', raw_id, payload)
            assert ('insert', raw_id, *expected) in materialized_values(conn)
            insert_raw(conn, 'update', raw_id, {})
            conn.execute("update raw_items set raw_payload=? where connector_id='update' and raw_item_id=?", (json.dumps(payload), raw_id))
            assert ('update', raw_id, *expected) in materialized_values(conn)
        conn.execute("update raw_items set raw_payload='{}'")
        assert set(materialized_values(conn)) == {
            (connector, str(i), 0, 0) for connector in ('insert', 'update') for i in range(len(PAYLOADS))
        }
        conn.execute("delete from raw_items where connector_id='insert'")
        assert set(materialized_values(conn)) == {('update', str(i), 0, 0) for i in range(len(PAYLOADS))}


def test_raw_item_upsert_updates_existing_flags_without_conflict(tmp_path):
    path = tmp_path / 'upsert.sqlite3'
    runner.migrate_database(path)
    with sqlite3.connect(path) as conn:
        insert_raw(conn, 'connector', 'raw', {})
        for payload, expected in ((PAYLOADS[-1][0], (1, 1)), ({}, (0, 0))):
            # An outer UPSERT's DO UPDATE overrides a trigger's OR REPLACE.
            conn.execute("""insert into raw_items select * from raw_items
                where connector_id='connector' and raw_item_id='raw'
                on conflict(connector_id, raw_item_id) do update set raw_payload=?""",
                (json.dumps(payload),))
            assert materialized_values(conn) == [('connector', 'raw', *expected)]


@pytest.mark.parametrize('drift', ['rowid', 'changed_trigger'])
def test_startup_rejects_candidate_flag_schema_drift(tmp_path, drift):
    path = tmp_path / 'drift.sqlite3'
    runner.migrate_database(path)
    with sqlite3.connect(path) as conn:
        if drift == 'rowid':
            conn.execute('drop table raw_item_candidate_flags')
            conn.execute(flags.SCHEMA_STATEMENTS[0].replace('without rowid', ''))
        else:
            name = 'raw_item_candidate_flags_after_update'
            conn.execute(f'drop trigger {name}')
            conn.execute(f"create trigger {name} after update of raw_payload on raw_items begin select 1; end")
    with pytest.raises(SchemaDriftError, match='raw_item_candidate_flags'):
        runner.migrate_database(path)
