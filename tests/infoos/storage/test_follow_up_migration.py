"""v16 upgrade, atomic retry and append-only follow-up constraints."""
import sqlite3

import pytest

from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database
from tests.infoos.storage.migration_test_helpers import drop_v17_schema


def test_upgrade_failure_resume_fresh_and_idempotent(tmp_path):
    path = tmp_path / "follow-up.sqlite3"
    migrate_database(path)
    with sqlite3.connect(path) as conn:
        drop_v17_schema(conn)
        conn.execute("pragma user_version=16")
    def fail(event):
        if event["migration"] == 17:
            raise RuntimeError("interrupted")
    with pytest.raises(RuntimeError, match="interrupted"):
        migrate_database(path, progress_callback=fail)
    with sqlite3.connect(path) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 16
    assert migrate_database(path).after_version == LATEST_VERSION
    assert migrate_database(path).batches == ()
    with sqlite3.connect(path) as conn:
        assert conn.execute("select count(*) from opportunity_follow_up_events").fetchone()[0] == 0
        assert conn.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert conn.execute("pragma foreign_key_check").fetchall() == []
