"""v23 read indexes on an actual v22 upgrade."""
import sqlite3

import pytest

from backend.infrastructure.storage.migrations import runner


def test_v22_upgrade_adds_indexes_with_degraded_membership(tmp_path):
    path = tmp_path / "indexes.sqlite3"

    def stop_before_v23(event):
        if event["migration"] == 23:
            raise RuntimeError("stop at v22")

    with pytest.raises(RuntimeError, match="stop at v22"):
        runner.migrate_database(path, progress_callback=stop_before_v23)
    with sqlite3.connect(path) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 22
        names = {r[0] for r in conn.execute("select name from sqlite_schema where type='index'")}
        assert not names.intersection({
            "idx_tasks_queue_created", "idx_card_content_blocks_degraded_flags",
            "idx_card_evaluations_passed_relevance",
        })
    runner.migrate_database(path)
    with sqlite3.connect(path) as conn:
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
