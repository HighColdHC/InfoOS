"""v25 thumbnail table creation, constraints and attachment deletion."""
import sqlite3

import pytest

from backend.infrastructure.storage.migrations import runner


@pytest.mark.parametrize("upgrade", [False, True])
def test_fresh_and_v24_upgrade(tmp_path, upgrade):
    path = tmp_path / "thumbnails.sqlite3"
    if upgrade:
        def stop(event):
            if event["migration"] == 25:
                raise RuntimeError("stop at v24")
        with pytest.raises(RuntimeError, match="stop at v24"):
            runner.migrate_database(path, progress_callback=stop)
        with sqlite3.connect(path) as conn:
            assert conn.execute("pragma user_version").fetchone()[0] == 24
            assert conn.execute("select name from sqlite_schema where name='card_asset_thumbnails'").fetchone() is None
    report = runner.migrate_database(path)
    assert (report.before_version, report.after_version) == (24 if upgrade else 0, 25)
    with sqlite3.connect(path) as conn:
        assert conn.execute("select count(*) from card_asset_thumbnails").fetchone()[0] == 0
        assert conn.execute("pragma foreign_key_check").fetchall() == []


def seed_asset(conn):
    conn.execute("""insert into cards(card_id,source_platform,source_type,source_url,title,content,captured_at,processed_at)
                    values ('card','rss','article','','Title','Body','now','now')""")
    conn.execute("""insert into card_assets(asset_id,card_id,asset_kind,source_url,status,created_at,updated_at)
                    values ('asset','card','image','','downloaded','now','now')""")


@pytest.mark.parametrize("status,path,width,height,size,error", [
    ("ready", "thumb.webp", None, 20, 30, None),
    ("failed", "thumb.webp", None, None, None, "ffmpeg_failed"),
    ("other", None, None, None, None, "ffmpeg_failed"),
])
def test_thumbnail_constraints(tmp_path, status, path, width, height, size, error):
    database = tmp_path / "constraints.sqlite3"
    runner.migrate_database(database)
    with sqlite3.connect(database) as conn:
        conn.execute("pragma foreign_keys=on")
        seed_asset(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""insert into card_asset_thumbnails
                (asset_id,status,local_path,width,height,size_bytes,error_code,created_at,updated_at)
                values ('asset',?,?,?,?,?,?,'now','now')""", (status, path, width, height, size, error))


def test_thumbnail_cascades_on_asset_delete(tmp_path):
    database = tmp_path / "cascade.sqlite3"
    runner.migrate_database(database)
    with sqlite3.connect(database) as conn:
        conn.execute("pragma foreign_keys=on")
        seed_asset(conn)
        conn.execute("""insert into card_asset_thumbnails
            (asset_id,status,error_code,created_at,updated_at)
            values ('asset','failed','source_missing','now','now')""")
        conn.execute("delete from card_assets where asset_id='asset'")
        assert conn.execute("select count(*) from card_asset_thumbnails").fetchone()[0] == 0
