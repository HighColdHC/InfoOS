"""v20 → v21 upgrade is atomic and preserves old evaluation semantics."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.migrations.runner import migrate_database
from tests.infoos.storage.migration_test_helpers import drop_v21_schema
from tests.infoos.test_agent_evaluations import seed
from backend.infrastructure.storage.card_evaluations import CardEvaluationRepository


def test_upgrade_rollback_old_rows_concurrent_start_and_repeat(tmp_path):
    seed(tmp_path)
    path = default_database_path(tmp_path)
    with sqlite3.connect(path) as conn:
        drop_v21_schema(conn)
        # A genuinely old evaluation has neither new column.
        conn.execute("""insert into card_evaluations
            (evaluation_id,card_id,card_version,profile_version,prompt_version,model,provider_id,quality,
             relevance,attribution_kind,content_type,suggested_action,paused_landmark,summary,relation_note,passed_quality,created_at)
            values ('old','card-1',1,1,'rubric_v1','test','test',80,80,'project','method','read',0,'摘要','关系',1,'2026-10-27T00:00:00Z')""")
        conn.execute("pragma user_version=20")
    def interrupt(event):
        if event["migration"] == 21:
            raise RuntimeError("interrupted")
    with pytest.raises(RuntimeError, match="interrupted"):
        migrate_database(path, progress_callback=interrupt)
    with sqlite3.connect(path) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 20
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(report.after_version == 21 for report in pool.map(migrate_database, [path, path]))
    assert migrate_database(path).batches == ()
    evaluation = CardEvaluationRepository(tmp_path).latest_for_card("card-1")
    assert evaluation["prompt_version"] == "rubric_v1" and evaluation["exclusion"] is None and evaluation["extract_kinds"] is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("pragma quick_check").fetchone()[0] == "ok"
        assert conn.execute("pragma foreign_key_check").fetchall() == []
