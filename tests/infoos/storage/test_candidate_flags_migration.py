"""v24 backfill and triggers preserve the v23 raw JSON eligibility rules."""
from concurrent.futures import ThreadPoolExecutor
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
    ({"raw_metadata": {"prefilter": {"status": "unknown"}}}, (1, 0)),
    ({"prefilter": None, "raw_metadata": {"prefilter": {"status": "matched"}}}, (0, 0)),
    ({"prefilter": {}, "raw_metadata": {"prefilter": {"status": "matched"}}}, (0, 0)),
    ({"prefilter": {"status": "rejected"}, "raw_metadata": {"prefilter": {"status": "matched"}}}, (0, 0)),
    ({"prefilter": {"status": "unknown"}, "raw_metadata": {"prefilter": {"status": "rejected"}}}, (1, 0)),
    *[({"prefilter": {"status": value}}, (0, 0)) for value in (None, 1, True, [], {})],
    *[({"raw_metadata": {"prefilter": {"status": value}}}, (0, 0)) for value in (None, 1, True, [], {})],
    ({"raw_metadata": {"tags": ["风向标"]}}, (0, 1)),
    ({"raw_metadata": {"tags": ["AI 风向标周报", 3, None, {"name": "风向标"}]}}, (0, 1)),
    ({"raw_metadata": {"tags": ["其他", 3, None, {"name": "风向标"}]}}, (0, 0)),
    *[({"raw_metadata": {"tags": value}}, (0, 0)) for value in (None, "风向标", {"name": "风向标"}, 1, [])],
    ({"prefilter": {"status": "matched"}, "raw_metadata": {"tags": ["风向标"]}}, (1, 1)),
]


def insert_raw(conn, connector, raw_id, payload):
    conn.execute("""insert into raw_items
        (connector_id,raw_item_id,source_platform,source_type,source_url,captured_at,
         raw_payload,first_seen_at,last_seen_at)
        values (?,?,'rss','article','','2026-10-29T00:00:00Z',?,
                '2026-10-29T00:00:00Z','2026-10-29T00:00:00Z')""",
        (connector, raw_id, json.dumps(payload)))


def legacy_values(conn):
    return conn.execute(f"""select r.connector_id,r.raw_item_id,
        case when {flags.PREFILTER_CANDIDATE} then 1 else 0 end,
        case when {flags.WIND_VANE_TAGGED} then 1 else 0 end
        from raw_items r order by r.connector_id,r.raw_item_id""").fetchall()


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


@pytest.mark.parametrize("upgrade", [False, True])
def test_fresh_and_real_v23_upgrade_backfill_repeat(tmp_path, upgrade):
    path = tmp_path / "flags.sqlite3"
    before = []
    if upgrade:
        make_v23(path)
        with sqlite3.connect(path) as conn:
            for i, (payload, expected) in enumerate(PAYLOADS):
                insert_raw(conn, 'connector', str(i), payload)
            before = conn.execute("select * from raw_items order by raw_item_id").fetchall()
    report = runner.migrate_database(path)
    assert (report.before_version, report.after_version) == (23 if upgrade else 0, 24)
    with sqlite3.connect(path) as conn:
        assert materialized_values(conn) == legacy_values(conn)
        if upgrade:
            assert conn.execute("select * from raw_items order by raw_item_id").fetchall() == before
            actual = {row[1]: row[2:] for row in materialized_values(conn)}
            assert actual == {str(i): expected for i, (_, expected) in enumerate(PAYLOADS)}
        assert conn.execute("pragma foreign_key_check").fetchall() == []
        snapshot = materialized_values(conn)
    assert runner.migrate_database(path).batches == ()
    with sqlite3.connect(path) as conn:
        assert materialized_values(conn) == snapshot


def test_upgrade_rollback_and_concurrent_restart(tmp_path):
    path = tmp_path / 'restart.sqlite3'
    make_v23(path)
    with sqlite3.connect(path) as conn:
        insert_raw(conn, 'connector', 'raw', PAYLOADS[-1][0])

    def interrupt(event):
        if event['migration'] == 24:
            raise RuntimeError('interrupted')

    with pytest.raises(RuntimeError, match='interrupted'):
        runner.migrate_database(path, progress_callback=interrupt)
    with sqlite3.connect(path) as conn:
        assert conn.execute('pragma user_version').fetchone()[0] == 23
        assert conn.execute("select name from sqlite_schema where name like 'raw_item_candidate_flags%'").fetchall() == []
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(report.after_version == 24 for report in pool.map(runner.migrate_database, [path, path]))
    with sqlite3.connect(path) as conn:
        assert materialized_values(conn) == [('connector', 'raw', 1, 1)]


@pytest.mark.parametrize('payload,expected', PAYLOADS)
def test_insert_and_payload_update_synchronize_flags(tmp_path, payload, expected):
    path = tmp_path / 'trigger.sqlite3'
    runner.migrate_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute('pragma foreign_keys=on')
        insert_raw(conn, 'insert', 'same-id', payload)
        insert_raw(conn, 'update', 'same-id', {"prefilter": {"status": "matched"}})
        conn.execute("update raw_items set raw_payload=? where connector_id='update'", (json.dumps(payload),))
        assert materialized_values(conn) == [('insert', 'same-id', *expected), ('update', 'same-id', *expected)]
        assert materialized_values(conn) == legacy_values(conn)
        # The same row can move out of either lane on subsequent observations.
        conn.execute("update raw_items set raw_payload='{}'")
        assert materialized_values(conn) == [('insert', 'same-id', 0, 0), ('update', 'same-id', 0, 0)]
        conn.execute("delete from raw_items where connector_id='insert'")
        assert materialized_values(conn) == [('update', 'same-id', 0, 0)]


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


@pytest.mark.parametrize('drift', ['table', 'rowid', 'fk', 'missing_trigger', 'changed_trigger'])
def test_startup_rejects_candidate_flag_schema_drift(tmp_path, drift):
    path = tmp_path / 'drift.sqlite3'
    runner.migrate_database(path)
    with sqlite3.connect(path) as conn:
        if drift == 'table':
            conn.execute('drop table raw_item_candidate_flags')
        elif drift in ('rowid', 'fk'):
            conn.execute('drop table raw_item_candidate_flags')
            sql = flags.SCHEMA_STATEMENTS[0]
            if drift == 'rowid':
                sql = sql.replace('without rowid', '')
            else:
                sql = sql.replace('on delete cascade', 'on delete restrict')
            conn.execute(sql)
        else:
            name = 'raw_item_candidate_flags_after_update'
            conn.execute(f'drop trigger {name}')
            if drift == 'changed_trigger':
                conn.execute(f"create trigger {name} after update of raw_payload on raw_items begin select 1; end")
    with pytest.raises(SchemaDriftError, match='raw_item_candidate_flags'):
        runner.migrate_database(path)
