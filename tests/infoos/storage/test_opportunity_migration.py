"""Fresh v12 and transactional upgrade from v11, including index/FK contracts."""
import sqlite3

import pytest

from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database


@pytest.mark.parametrize("upgrade", [False, True])
def test_opportunity_schema(tmp_path, upgrade):
    db = tmp_path / "opportunity.sqlite3"
    if upgrade:
        def stop(event):
            if event["migration"] == 12:
                raise RuntimeError("pre-v12")
        with pytest.raises(RuntimeError, match="pre-v12"):
            migrate_database(db, progress_callback=stop)
        with sqlite3.connect(db) as conn:
            assert conn.execute("pragma user_version").fetchone()[0] == 11
            assert not conn.execute("select name from sqlite_schema where name='opportunity_breakdowns'").fetchone()
    report = migrate_database(db)
    assert (report.before_version, report.after_version) == (11 if upgrade else 0, LATEST_VERSION)
    with sqlite3.connect(db) as conn:
        columns = {r[1] for r in conn.execute("pragma table_info(opportunity_breakdowns)")}
        assert columns == {"breakdown_id", "card_id", "card_version", "profile_version", "raw_item_revision", "lens_version", "rules_version", "evaluator",
                           "token_id", "monetization_model", "offer", "price_signal", "funnel", "target_customer",
                           "evidence", "replicability", "credibility", "risk_flags", "next_step", "verify_questions",
                           "breakdown_note", "created_at", "writeback_at"}
        for name, fields in (("idx_opportunity_breakdowns_card", ["card_id", "created_at"]),
                             ("idx_opportunity_breakdowns_next_step", ["next_step", "created_at"]),
                             ("idx_opportunity_breakdowns_created", ["created_at", "breakdown_id"])):
            keys = [r for r in conn.execute(f"pragma index_xinfo({name})") if r[5]]
            assert [r[2] for r in keys] == fields and keys[-1][3] == 1
        assert conn.execute("pragma foreign_key_list(opportunity_breakdowns)").fetchone()[2:5] == ("cards", "card_id", "card_id")
        assert conn.execute("pragma quick_check").fetchone()[0] == "ok"
    assert migrate_database(db).before_version == LATEST_VERSION
