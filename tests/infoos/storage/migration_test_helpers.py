"""Path-shaped test helper for callers that previously used the old initializer."""

import sqlite3
from pathlib import Path

from backend.infrastructure.storage.migrations.runner import migrate_database


def migrate_database_path(path: Path | str) -> Path:
    return migrate_database(path).path


def drop_v8_schema(conn: sqlite3.Connection) -> None:
    """Remove v0008 schema before simulating an earlier database version."""
    drop_v9_indexes(conn)
    conn.execute("drop index if exists idx_card_evaluations_window")
    conn.execute("drop index if exists idx_card_evaluations_card_latest")
    conn.execute("drop table if exists daily_briefs")
    conn.execute("drop table if exists card_evaluations")
    conn.execute("drop table if exists interest_profiles")


def drop_post_v3_schema(conn: sqlite3.Connection) -> None:
    """Remove schema added after v0003 so tests can simulate older databases."""
    drop_v8_schema(conn)
    conn.execute("drop index if exists idx_raw_items_first_seen_source")
    token_columns = {row[1] for row in conn.execute("pragma table_info(api_tokens)")}
    if "webhook_include_backfill" in token_columns:
        conn.execute("alter table api_tokens drop column webhook_include_backfill")
    conn.execute("drop table if exists raw_item_revisions")
    columns = {row[1] for row in conn.execute("pragma table_info(raw_items)")}
    for column in ("content_hash", "revision"):
        if column in columns:
            conn.execute(f"alter table raw_items drop column {column}")
    conn.execute("drop table if exists provider_receipt_attempts")
    conn.execute("drop table if exists provider_receipts")


def drop_v9_indexes(conn: sqlite3.Connection) -> None:
    """Remove additive read indexes before simulating a pre-v9 database."""
    drop_v15_schema(conn)
    conn.execute("drop table if exists opportunity_verifications")
    conn.execute("drop table if exists opportunity_breakdowns")
    conn.execute("drop index if exists idx_cards_opportunity_raw")
    conn.execute("drop table if exists source_change_audit")
    for name in ("idx_cards_active_raw",):
        conn.execute(f"drop index if exists {name}")


def drop_v15_schema(conn: sqlite3.Connection) -> None:
    """Remove v15 additions before lowering user_version in historical fixtures."""
    drop_v16_schema(conn)
    from backend.infrastructure.storage.migrations.v0015_card_read_keys import TRIGGERS
    for name in TRIGGERS:
        conn.execute(f'drop trigger if exists {name}')
    conn.execute('drop table if exists card_text_fts')
    conn.execute('drop table if exists card_block_text_fts')
    conn.execute('drop index if exists idx_cards_status_sort')
    conn.execute('drop index if exists idx_cards_status_published')
    columns = {row[1] for row in conn.execute('pragma table_info(cards)')}
    for column in ('sort_ts', 'published_ts'):
        if column in columns:
            conn.execute(f'alter table cards drop column {column}')


def drop_v25_schema(conn: sqlite3.Connection) -> None:
    conn.execute("drop table if exists card_asset_thumbnails")


def drop_v24_schema(conn: sqlite3.Connection) -> None:
    drop_v25_schema(conn)
    from backend.infrastructure.storage.migrations.v0024_candidate_flags import TRIGGERS
    for name in TRIGGERS:
        conn.execute(f'drop trigger if exists {name}')
    conn.execute('drop table if exists raw_item_candidate_flags')


def drop_v23_indexes(conn: sqlite3.Connection) -> None:
    drop_v24_schema(conn)
    for name in ("idx_tasks_queue_created", "idx_card_content_blocks_degraded_flags",
                 "idx_card_evaluations_passed_relevance"):
        conn.execute(f"drop index if exists {name}")


def drop_v21_schema(conn: sqlite3.Connection) -> None:
    drop_v23_indexes(conn)
    for table in ("knowledge_status_events", "knowledge_evidence", "knowledge_cards", "knowledge_extractions"):
        conn.execute(f"drop table if exists {table}")
    columns = {row[1] for row in conn.execute("pragma table_info(card_evaluations)")}
    for column in ("exclusion", "extract_kinds"):
        if column in columns:
            conn.execute(f"alter table card_evaluations drop column {column}")


def drop_v17_schema(conn: sqlite3.Connection) -> None:
    drop_v21_schema(conn)
    conn.execute("drop table if exists reader_page_views")
    conn.execute('drop table if exists opportunity_follow_up_events')


def drop_v16_schema(conn: sqlite3.Connection) -> None:
    drop_v17_schema(conn)
    conn.execute('drop table if exists notification_outbox')
    conn.execute('drop table if exists auth_qr_rounds')
    for table in ('card_evaluations', 'opportunity_breakdowns'):
        columns = {row[1] for row in conn.execute(f'pragma table_info({table})')}
        if 'writeback_at' in columns:
            conn.execute(f'alter table {table} drop column writeback_at')
