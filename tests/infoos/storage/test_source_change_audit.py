"""v0011 fresh creation, v0010 upgrade, and rollback/repeatability."""
import sqlite3

import pytest

from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database


@pytest.mark.parametrize("upgrade", [False, True])
def test_source_audit_schema(tmp_path, upgrade):
    db = tmp_path / "audit.sqlite3"
    if upgrade:
        def stop(event):
            if event["migration"] == 11:
                raise RuntimeError("pre-v11")
        with pytest.raises(RuntimeError, match="pre-v11"):
            migrate_database(db, progress_callback=stop)
        with sqlite3.connect(db) as conn:
            assert conn.execute("pragma user_version").fetchone()[0] == 10
            assert not conn.execute("select name from sqlite_schema where name='source_change_audit'").fetchone()
    report = migrate_database(db)
    assert (report.before_version, report.after_version) == (10 if upgrade else 0, LATEST_VERSION)
    with sqlite3.connect(db) as conn:
        columns = {r[1] for r in conn.execute("pragma table_info(source_change_audit)")}
        assert columns == {"audit_id", "source_id", "action", "actor_kind", "token_id", "reason",
                           "before_config_version", "after_config_version", "created_at"}
        for name, fields in (("idx_source_change_audit_source", ["source_id", "created_at"]),
                             ("idx_source_change_audit_created", ["created_at"])):
            index = list(conn.execute(f"pragma index_xinfo({name})"))
            keys = [row for row in index if row[5]]
            assert [row[2] for row in keys] == fields
            assert keys[-1][3] == 1  # created_at DESC
    assert migrate_database(db).before_version == LATEST_VERSION
