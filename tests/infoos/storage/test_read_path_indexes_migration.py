"""v23 read indexes on fresh databases and actual v22 upgrade boundaries."""
import sqlite3

import pytest

from backend.infrastructure.storage.migrations import runner


INDEX_KEYS = {
    "idx_tasks_queue_created": [("queue_name", 0), ("created_at", 1), ("task_id", 0)],
    "idx_card_content_blocks_degraded_flags": [("card_id", 0), ("status", 0), ("provenance", 0)],
    "idx_card_evaluations_passed_relevance": [("passed_quality", 0), ("relevance", 1), ("quality", 1)],
}


@pytest.mark.parametrize("upgrade", [False, True])
def test_fresh_and_v22_upgrade_add_indexes_with_degraded_membership(tmp_path, upgrade):
    path = tmp_path / "indexes.sqlite3"
    if upgrade:
        def stop_before_v23(event):
            if event["migration"] == 23:
                raise RuntimeError("stop at v22")

        with pytest.raises(RuntimeError, match="stop at v22"):
            runner.migrate_database(path, progress_callback=stop_before_v23)
        with sqlite3.connect(path) as conn:
            assert conn.execute("pragma user_version").fetchone()[0] == 22
            assert not (set(INDEX_KEYS) & {r[0] for r in conn.execute(
                "select name from sqlite_schema where type='index'")})
    report = runner.migrate_database(path)
    assert (report.before_version, report.after_version) == (22 if upgrade else 0, runner.LATEST_VERSION)
    assert runner.migrate_database(path).batches == ()
    with sqlite3.connect(path) as conn:
        for name, expected in INDEX_KEYS.items():
            assert [(r[2], r[3]) for r in conn.execute(f"pragma index_xinfo({name})") if r[5]] == expected
        flags = conn.execute("pragma index_list(card_content_blocks)").fetchall()
        assert next(r[4] for r in flags if r[1] == "idx_card_content_blocks_degraded_flags") == 1
        conn.execute("""insert into cards(card_id, source_platform, source_type, source_url,
                       title, content, captured_at, processed_at)
                       values ('card', 'rss', 'article', '', '', '', '', '')""")
        blocks = [("healthy", "preserved", ""), ("fallback", "preserved", "METADATA_FALLBACK"),
                  ("failed", "Failed", ""), ("error", "error", ""),
                  ("blocked", "BLOCKED", ""), ("pending", "pending", "")]
        conn.executemany("""insert into card_content_blocks
            (block_id, card_id, layer, block_kind, status, provenance, created_at, updated_at)
            values (?, 'card', 'original', 'article', ?, ?, '', '')""", blocks)
        # INDEXED BY makes a mismatched partial predicate fail instead of
        # silently scanning another index; rows check its actual membership.
        indexed = conn.execute("""select status, provenance from card_content_blocks
            indexed by idx_card_content_blocks_degraded_flags
            where instr(lower(coalesce(provenance, '')), 'metadata_fallback') > 0
               or lower(status) in ('failed', 'error', 'blocked', 'pending')""").fetchall()
        assert set(indexed) == {(status, provenance) for _, status, provenance in blocks[1:]}
        assert runner._validate_latest_schema(conn, include_v19=True, include_v21=True, include_v23=True) == []


def test_read_index_drift_reports_partial_predicate_mismatch(tmp_path):
    path = tmp_path / "drift.sqlite3"
    runner.migrate_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("drop index idx_card_content_blocks_degraded_flags")
        conn.execute("""create index idx_card_content_blocks_degraded_flags
                        on card_content_blocks(card_id, status, provenance)
                        where status = 'failed'""")
        warnings = runner._validate_latest_schema(conn, include_v19=True, include_v21=True, include_v23=True)
        assert len(warnings) == 1
        assert "idx_card_content_blocks_degraded_flags" in warnings[0]
