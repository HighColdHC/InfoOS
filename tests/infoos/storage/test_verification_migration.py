"""Fresh schema and rollback-safe v12 to v13 upgrade."""
import sqlite3

import pytest

from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database
from backend.domain.intelligence.verification import FIELDS


@pytest.mark.parametrize("upgrade", [False, True])
def test_verification_schema(tmp_path, upgrade):
    db = tmp_path / "verification.sqlite3"
    if upgrade:
        def stop(event):
            if event["migration"] == 13:
                raise RuntimeError("pre-v13")
        with pytest.raises(RuntimeError, match="pre-v13"):
            migrate_database(db, progress_callback=stop)
        with sqlite3.connect(db) as conn:
            assert conn.execute("pragma user_version").fetchone()[0] == 12
            assert not conn.execute("select name from sqlite_schema where name='opportunity_verifications'").fetchone()
    result = migrate_database(db)
    assert (result.before_version, result.after_version) == (12 if upgrade else 0, LATEST_VERSION)
    with sqlite3.connect(db) as conn:
        assert {r[1] for r in conn.execute("pragma table_info(opportunity_verifications)")} == {
            *FIELDS, "verification_id", "token_id", "schema_version", "created_at",
            "client_verification_id", "request_content_hash", "breakdown_id", "expected_breakdown_hash", "breakdown_snapshot"}
        for suffix, fields in (("card", ["card_id", "created_at"]), ("conclusion", ["conclusion", "created_at"]),
                               ("created", ["created_at"])):
            index = f"idx_opportunity_verifications_{suffix}"
            assert [r[2] for r in conn.execute(f"pragma index_info({index})")] == fields
            keys = [r for r in conn.execute(f"pragma index_xinfo({index})") if r[5]]
            assert keys[-1][3] == 1
        assert conn.execute("pragma quick_check").fetchone()[0] == "ok"
    assert migrate_database(db).before_version == LATEST_VERSION
