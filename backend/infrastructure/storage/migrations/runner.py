"""Ordered, process-safe SQLite migration runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time
from typing import Any

from . import v0001_baseline, v0002_legacy_backfills, v0003_task_read_indexes, v0010_captured_at_backfill, v0015_card_read_keys
from .v0001_baseline import SchemaDriftError as SchemaDriftError
from .v0002_legacy_backfills import LegacyMigrationMarkerError as LegacyMigrationMarkerError


LATEST_VERSION = 21
DEFAULT_BATCH_SIZE = v0002_legacy_backfills.DEFAULT_BATCH_SIZE
ProgressCallback = Callable[[dict[str, Any]], None]


def _validate_latest_schema(conn: sqlite3.Connection, *, include_v15: bool = True, include_v16: bool = True, include_v17: bool = True, include_v19: bool = False, include_v21: bool = False) -> list[str]:
    statements = [
        *v0001_baseline._statements(),
        *(sql + ";" for _name, sql in v0003_task_read_indexes.INDEXES),
        *_receipt_statements(),
        *_raw_revision_statements(),
        *_backfill_statements(),
        *_cadence_statements(),
        *_intelligence_statements(),
        *_entry_read_statements(),
        *_sql_statements("v0011_source_change_audit.sql"),
        *_sql_statements("v0012_opportunity_breakdowns.sql"),
        *_sql_statements("v0013_opportunity_verifications.sql"),
        *_sql_statements("v0014_opportunity_context.sql"),
    ]
    if include_v15:
        statements.extend(v0015_card_read_keys.STATEMENTS)
    if include_v16:
        statements.extend(_sql_statements("v0016_notification_outbox.sql"))
    if include_v17:
        statements.extend(_sql_statements("v0017_opportunity_follow_ups.sql"))
    if include_v19:
        statements.extend(_sql_statements("v0019_reader_page_views.sql"))
    if include_v21:
        statements.extend(_sql_statements("v0021_knowledge_cards.sql"))
    warnings = v0001_baseline.validate_schema(conn, statements=statements)
    if include_v15:
        v0015_card_read_keys.validate(conn)
    v0003_task_read_indexes.validate(conn)
    return warnings


def _sql_statements(filename: str) -> list[str]:
    path = Path(__file__).with_name(filename)
    statements: list[str] = []
    buffer = ""
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise DatabaseMigrationError(f"incomplete migration {filename}")
    return statements


def _receipt_statements() -> list[str]:
    return _sql_statements("v0004_provider_receipts.sql")


def _raw_revision_statements() -> list[str]:
    return _sql_statements("v0005_raw_item_revisions.sql")


def _backfill_statements() -> list[str]:
    return _sql_statements("v0006_webhook_backfill.sql")


def _cadence_statements() -> list[str]:
    return _sql_statements("v0007_source_cadence_index.sql")


def _intelligence_statements() -> list[str]:
    return _sql_statements("v0008_intelligence.sql")


def _entry_read_statements() -> list[str]:
    return _sql_statements("v0009_entry_read_indexes.sql")


class DatabaseMigrationError(RuntimeError):
    """Base class for migration and readiness failures."""


class DatabaseVersionTooNew(DatabaseMigrationError):
    """The database was written by a newer InfoOS schema version."""

    def __init__(self, path: Path | str, database_version: int, latest_version: int = LATEST_VERSION):
        self.path = Path(path)
        self.database_version = int(database_version)
        self.latest_version = int(latest_version)
        super().__init__(
            f"database {self.path} has user_version={self.database_version}, "
            f"but this code supports at most {self.latest_version}"
        )


class DatabaseNotReady(DatabaseMigrationError):
    """A business path opened a database that has not completed migration."""

    def __init__(self, path: Path | str, database_version: int | None, latest_version: int = LATEST_VERSION):
        self.path = Path(path)
        self.database_version = database_version
        self.latest_version = int(latest_version)
        detail = "does not exist" if database_version is None else f"has user_version={database_version}"
        super().__init__(
            f"database {self.path} {detail}; explicit startup migration must reach "
            f"user_version={self.latest_version} before business access"
        )


@dataclass(frozen=True)
class MigrationReport:
    path: Path
    before_version: int
    after_version: int
    batches: tuple[dict[str, Any], ...]


def read_user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("pragma user_version").fetchone()[0])


def _begin_immediate(conn: sqlite3.Connection, *, attempts: int = 3) -> None:
    for attempt in range(attempts):
        try:
            conn.execute("begin immediate")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt + 1 == attempts:
                raise
            time.sleep(0.25 * (attempt + 1))


def _set_wal_mode(conn: sqlite3.Connection, *, attempts: int = 8) -> None:
    """Set WAL mode despite concurrent startup PRAGMAs from sibling processes."""
    for attempt in range(attempts):
        try:
            conn.execute("pragma journal_mode = wal")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt + 1 == attempts:
                raise
            time.sleep(0.1 * (attempt + 1))


def _open_migration_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 60000")
        _set_wal_mode(conn)
        conn.execute("pragma foreign_keys = on")
        conn.execute("pragma synchronous = normal")
        return conn
    except Exception:
        conn.close()
        raise


def migrate_database(
    db_path: Path | str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_callback: ProgressCallback | None = None,
) -> MigrationReport:
    """Migrate one database to ``LATEST_VERSION`` using short write transactions.

    The callback runs before its batch commits. Raising from it deliberately rolls
    back that batch, which also provides deterministic interruption testing.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    path = Path(db_path).expanduser().resolve()
    conn = _open_migration_connection(path)
    events: list[dict[str, Any]] = []
    schema_validated = False
    try:
        before_version = read_user_version(conn)
        if before_version > LATEST_VERSION:
            raise DatabaseVersionTooNew(path, before_version)

        while True:
            _begin_immediate(conn)
            try:
                current = read_user_version(conn)
                if current > LATEST_VERSION:
                    raise DatabaseVersionTooNew(path, current)
                if current == LATEST_VERSION:
                    warnings = _validate_latest_schema(conn, include_v19=True, include_v21=True)
                    schema_validated = True
                    if warnings:
                        event = {
                            "migration": 1,
                            "phase": "schema_warnings",
                            "rows": 0,
                            "complete": True,
                            "warnings": warnings,
                        }
                        if progress_callback is not None:
                            progress_callback(event)
                        events.append(event)
                    conn.commit()
                    break
                if current == 0:
                    warnings = v0001_baseline.apply(conn)
                    schema_validated = True
                    event = {
                        "migration": 1,
                        "phase": "baseline",
                        "rows": 0,
                        "complete": True,
                        "warnings": warnings,
                    }
                    conn.execute("pragma user_version = 1")
                elif current == 1:
                    if not schema_validated:
                        v0001_baseline.validate_schema(conn)
                        schema_validated = True
                    batch = v0002_legacy_backfills.run_next_batch(conn, batch_size)
                    if batch is None:
                        conn.execute("pragma user_version = 2")
                        event = {
                            "migration": 2,
                            "phase": "complete",
                            "rows": 0,
                            "complete": True,
                            "cursor": None,
                        }
                    else:
                        event = batch.as_event()
                elif current == 2:
                    if not schema_validated:
                        v0001_baseline.validate_schema(conn)
                        schema_validated = True
                    v0003_task_read_indexes.apply(conn)
                    v0003_task_read_indexes.validate(conn)
                    conn.execute("pragma user_version = 3")
                    event = {
                        "migration": 3,
                        "phase": "task_read_indexes",
                        "rows": 0,
                        "complete": True,
                    }
                elif current == 3:
                    if not schema_validated:
                        v0001_baseline.validate_schema(conn)
                        v0003_task_read_indexes.validate(conn)
                        schema_validated = True
                    for statement in _receipt_statements():
                        conn.execute(statement)
                    conn.execute("pragma user_version = 4")
                    event = {"migration": 4, "phase": "provider_receipts", "rows": 0, "complete": True}
                elif current == 4:
                    if not schema_validated:
                        v0001_baseline.validate_schema(
                            conn,
                            statements=[*v0001_baseline._statements(),
                                        *(sql + ";" for _name, sql in v0003_task_read_indexes.INDEXES),
                                        *_receipt_statements()],
                        )
                        v0003_task_read_indexes.validate(conn)
                        schema_validated = True
                    for statement in _raw_revision_statements():
                        conn.execute(statement)
                    conn.execute("pragma user_version = 5")
                    event = {"migration": 5, "phase": "raw_item_revisions", "rows": 0, "complete": True}
                elif current == 5:
                    for statement in _backfill_statements():
                        conn.execute(statement)
                    conn.execute("pragma user_version = 6")
                    event = {"migration": 6, "phase": "webhook_backfill", "rows": 0, "complete": True}
                elif current == 6:
                    for statement in _cadence_statements():
                        conn.execute(statement)
                    conn.execute("pragma user_version = 7")
                    event = {"migration": 7, "phase": "source_cadence_index", "rows": 0, "complete": True}
                elif current == 7:
                    for statement in _intelligence_statements():
                        conn.execute(statement)
                    conn.execute("pragma user_version = 8")
                    event = {"migration": 8, "phase": "intelligence_tables", "rows": 0, "complete": True}
                elif current == 8:
                    for statement in _entry_read_statements():
                        conn.execute(statement)
                    conn.execute("pragma user_version = 9")
                    event = {"migration": 9, "phase": "entry_read_indexes", "rows": 0, "complete": True}
                elif current == 9:
                    batch = v0010_captured_at_backfill.run_next_batch(conn, batch_size)
                    if batch is None:
                        conn.execute("pragma user_version = 10")
                        event = {"migration": 10, "phase": "complete", "rows": 0, "complete": True}
                    else:
                        event = batch.as_event()
                elif current == 10:
                    for statement in _sql_statements("v0011_source_change_audit.sql"):
                        conn.execute(statement)
                    conn.execute("pragma user_version = 11")
                    event = {"migration": 11, "phase": "source_change_audit", "rows": 0, "complete": True}
                elif current == 11:
                    for statement in _sql_statements("v0012_opportunity_breakdowns.sql"):
                        conn.execute(statement)
                    conn.execute("pragma user_version = 12")
                    event = {"migration": 12, "phase": "opportunity_breakdowns", "rows": 0, "complete": True}
                elif current == 12:
                    for statement in _sql_statements("v0013_opportunity_verifications.sql"):
                        conn.execute(statement)
                    conn.execute("pragma user_version = 13")
                    event = {"migration": 13, "phase": "opportunity_verifications", "rows": 0, "complete": True}
                elif current == 13:
                    for statement in _sql_statements("v0014_opportunity_context.sql"):
                        conn.execute(statement)
                    conn.execute("pragma user_version = 14")
                    event = {"migration": 14, "phase": "opportunity_context", "rows": 0, "complete": True}
                elif current == 14:
                    if not schema_validated:
                        _validate_latest_schema(conn, include_v15=v0015_card_read_keys.has_progress(conn), include_v16=False, include_v17=False)
                        schema_validated = True
                    event = v0015_card_read_keys.run_next_batch(conn, batch_size)
                    if event["complete"]:
                        _validate_latest_schema(conn, include_v16=False, include_v17=False)
                        conn.execute("pragma user_version = 15")
                elif current == 15:
                    _validate_latest_schema(conn, include_v16=False, include_v17=False)
                    for statement in _sql_statements("v0016_notification_outbox.sql"):
                        conn.execute(statement)
                    _validate_latest_schema(conn, include_v17=False)
                    conn.execute("pragma user_version = 16")
                    event = {"migration": 16, "phase": "notification_outbox", "rows": 0, "complete": True}
                elif current == 16:
                    _validate_latest_schema(conn, include_v17=False)
                    for statement in _sql_statements("v0017_opportunity_follow_ups.sql"):
                        conn.execute(statement)
                    _validate_latest_schema(conn)
                    conn.execute("pragma user_version = 17")
                    event = {"migration": 17, "phase": "opportunity_follow_ups", "rows": 0, "complete": True}
                elif current == 17:
                    # v18 only updates data; its schema validation set remains v17.
                    _validate_latest_schema(conn)
                    for statement in _sql_statements("v0018_retire_scys_auth.sql"):
                        conn.execute(statement)
                    _validate_latest_schema(conn)
                    conn.execute("pragma user_version = 18")
                    event = {"migration": 18, "phase": "retire_scys_auth", "rows": 0, "complete": True}
                elif current == 18:
                    _validate_latest_schema(conn)
                    for statement in _sql_statements("v0019_reader_page_views.sql"):
                        conn.execute(statement)
                    _validate_latest_schema(conn, include_v19=True)
                    conn.execute("pragma user_version = 19")
                    event = {"migration": 19, "phase": "reader_page_views", "rows": 0, "complete": True}
                elif current == 19:
                    _validate_latest_schema(conn, include_v19=True)
                    for statement in _sql_statements("v0020_retire_feed_provider.sql"):
                        conn.execute(statement)
                    _validate_latest_schema(conn, include_v19=True)
                    conn.execute("pragma user_version = 20")
                    event = {"migration": 20, "phase": "retire_feed_provider", "rows": 0, "complete": True}
                elif current == 20:
                    _validate_latest_schema(conn, include_v19=True)
                    for statement in _sql_statements("v0021_knowledge_cards.sql"):
                        conn.execute(statement)
                    _validate_latest_schema(conn, include_v19=True, include_v21=True)
                    conn.execute("pragma user_version = 21")
                    event = {"migration": 21, "phase": "knowledge_cards", "rows": 0, "complete": True}
                else:
                    raise DatabaseMigrationError(f"unsupported database version {current} at {path}")
                if progress_callback is not None:
                    progress_callback(event)
                events.append(event)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return MigrationReport(
            path=path,
            before_version=before_version,
            after_version=read_user_version(conn),
            batches=tuple(events),
        )
    finally:
        conn.close()
