"""Fresh schema and rollback-safe v12 to v13 upgrade."""
import json
import sqlite3

import pytest

from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database
from backend.domain.intelligence.verification import FIELDS, validate_verification

from backend.infrastructure.storage.opportunity_breakdowns import COLUMNS
from tests.infoos.test_daily_intelligence import _card
from tests.infoos.test_opportunity_domain import item as breakdown_item
from tests.infoos.test_verification_domain import item

START = "2026-09-28T12:00:00Z"


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


def test_v13_data_upgrade_and_repeat_preserve_unknown_history(tmp_path):
    db = tmp_path / "v13.sqlite3"
    def stop(event):
        if event["migration"] == 14:
            raise RuntimeError("pre-v14")
    with pytest.raises(RuntimeError, match="pre-v14"):
        migrate_database(db, progress_callback=stop)
    _card(db, 1, START, evaluated=False)
    old_breakdown = {**breakdown_item(), "breakdown_id": "old-b", "card_version": 1,
                     "lens_version": "opportunity_v1", "rules_version": "old", "evaluator": "old",
                     "token_id": "writer", "created_at": START}
    columns = [c for c in COLUMNS if c not in {"profile_version", "raw_item_revision", "writeback_at"}]
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 13
        conn.execute(f"insert into opportunity_breakdowns ({','.join(columns)}) values ({','.join('?' for _ in columns)})",
                     [json.dumps(old_breakdown[c]) if isinstance(old_breakdown[c], list) else old_breakdown[c] for c in columns])
        old_record = {**validate_verification(item()), "verification_id": "old-v", "token_id": "writer",
                      "schema_version": "verification_v1", "created_at": START}
        columns = [*FIELDS, "verification_id", "token_id", "schema_version", "created_at"]
        conn.execute(f"insert into opportunity_verifications ({','.join(columns)}) values ({','.join('?' for _ in columns)})",
                     [old_record[c] for c in columns])
    assert migrate_database(db).after_version == LATEST_VERSION
    assert migrate_database(db).before_version == LATEST_VERSION
    with sqlite3.connect(db) as conn:
        assert conn.execute("select breakdown_id,profile_version,raw_item_revision from opportunity_breakdowns").fetchall() == [("old-b", None, None)]
        assert conn.execute("select verification_id,client_verification_id,request_content_hash,breakdown_id,expected_breakdown_hash,breakdown_snapshot from opportunity_verifications").fetchall() == [("old-v", None, None, None, None, None)]
        assert conn.execute("pragma quick_check").fetchone()[0] == "ok"
        index = next(r for r in conn.execute("pragma index_list(opportunity_verifications)") if r[1] == "idx_opportunity_verifications_client")
        assert index[2] == 1
        assert [r[2] for r in conn.execute("pragma index_info(idx_opportunity_verifications_client)")] == ["token_id", "client_verification_id"]
