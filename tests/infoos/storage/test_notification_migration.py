import sqlite3
import pytest

from backend.infrastructure.storage.migrations.runner import migrate_database, LATEST_VERSION
from tests.infoos.storage.migration_test_helpers import drop_v16_schema


def test_v15_upgrade_atomic_failure_resume_and_integrity(tmp_path):
    db = tmp_path / 'upgrade.sqlite3'
    migrate_database(db)
    with sqlite3.connect(db) as conn:
        drop_v16_schema(conn)
        conn.execute('pragma user_version=15')
    def interrupt(event):
        if event['migration'] == 16:
            raise RuntimeError('injected interruption')
    with pytest.raises(RuntimeError, match='injected interruption'):
        migrate_database(db, progress_callback=interrupt)
    with sqlite3.connect(db) as conn:
        assert conn.execute('pragma user_version').fetchone()[0] == 15
    report = migrate_database(db)
    assert (report.before_version, report.after_version) == (15, LATEST_VERSION)
    migrate_database(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('pragma integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('pragma foreign_key_check').fetchall() == []
        assert conn.execute('select count(*) from notification_outbox').fetchone()[0] == 0
        assert conn.execute('select count(*) from auth_qr_rounds').fetchone()[0] == 0
