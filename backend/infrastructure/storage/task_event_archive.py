"""Bounded, resumable cold storage for historical task events."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.storage.database import connect_existing, connect_readonly, default_database_path
from backend.infrastructure.storage.paths import resolve_data_dir


TASK_EVENT_HOT_WINDOW_DAYS = 30
TASK_EVENT_CRITICAL_HOT_WINDOW_DAYS = 180
TASK_EVENT_ARCHIVE_BATCH_SIZE = 2_000
TASK_EVENT_ARCHIVE_MAX_ROWS = 50_000
TASK_EVENT_ARCHIVE_MAX_SECONDS = 300
TASK_EVENT_ARCHIVE_PROGRESS_KEY = "maintenance.task_event_archive"

EVENT_COLUMNS = (
    "event_id",
    "task_id",
    "chain_id",
    "event_type",
    "status",
    "failure_class",
    "payload",
    "occurred_at",
)
BudgetCheck = Callable[[], bool]


def _check_budget(expired: BudgetCheck | None) -> None:
    if expired is not None and expired():
        raise TimeoutError("task event archive time budget exhausted")


def _bound_sql(conn: sqlite3.Connection, expired: BudgetCheck | None) -> None:
    if expired is not None:
        conn.set_progress_handler(lambda: int(expired()), 1000)


def _optional_scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...], expired: BudgetCheck | None) -> int | None:
    if expired is not None and expired():
        return None
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except sqlite3.OperationalError:
        if expired is not None and expired():
            return None
        raise


def _positive_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def hot_window_days() -> int:
    return _positive_env("INFOOS_TASK_EVENT_HOT_WINDOW_DAYS", TASK_EVENT_HOT_WINDOW_DAYS)


def critical_hot_window_days() -> int:
    return _positive_env(
        "INFOOS_TASK_EVENT_CRITICAL_HOT_WINDOW_DAYS",
        TASK_EVENT_CRITICAL_HOT_WINDOW_DAYS,
    )


def archive_batch_size() -> int:
    return _positive_env("INFOOS_TASK_EVENT_ARCHIVE_BATCH_SIZE", TASK_EVENT_ARCHIVE_BATCH_SIZE)


def archive_max_rows() -> int:
    return _positive_env("INFOOS_TASK_EVENT_ARCHIVE_MAX_ROWS", TASK_EVENT_ARCHIVE_MAX_ROWS)


def archive_max_seconds() -> int:
    return _positive_env("INFOOS_TASK_EVENT_ARCHIVE_MAX_SECONDS", TASK_EVENT_ARCHIVE_MAX_SECONDS)


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _critical_event_sql(alias: str = "e") -> str:
    return (
        f"(substr({alias}.event_type, 1, 14) = 'auth_recovery_' "
        f"or substr({alias}.event_type, 1, 18) = 'provider_recovery_' "
        f"or substr({alias}.event_type, 1, 8) = 'circuit_' "
        f"or substr({alias}.event_type, 1, 8) = 'failure_' "
        f"or {alias}.event_type in ('failed','blocked','lease_recovered','superseded'))"
    )


def _eligibility_sql(*, id_filter: bool = False) -> str:
    id_clause = "and e.event_id in (select event_id from archive_delete_ids)" if id_filter else ""
    return f"""
        source_timestamp(e.occurred_at) is not null
        and (({_critical_event_sql()} and julianday(source_timestamp(e.occurred_at)) < julianday(?))
          or (not {_critical_event_sql()} and julianday(source_timestamp(e.occurred_at)) < julianday(?)))
        {id_clause}
        and (
          e.task_id is null
          or not exists (select 1 from tasks t where t.task_id = e.task_id)
          or exists (
            select 1 from tasks t
            where t.task_id = e.task_id
              and t.status in ('success','failed','blocked','skipped')
          )
        )
        and (
          e.task_id is null
          or not exists (
            select 1
            from auth_recovery_incident_tasks linked
            join auth_recovery_incidents incident on incident.incident_id = linked.incident_id
            where linked.task_id = e.task_id and incident.status != 'resolved'
          )
        )
    """


def _cutoffs(now: datetime) -> tuple[str, str]:
    current = _utc(now)
    return (
        _iso(current - timedelta(days=critical_hot_window_days())),
        _iso(current - timedelta(days=hot_window_days())),
    )


def stable_event_checksum(rows: Iterable[dict[str, Any] | sqlite3.Row]) -> str:
    """Hash all task-event columns in event-id order with an unambiguous encoding."""
    digest = hashlib.sha256()
    normalized = sorted(
        ([row[column] for column in EVENT_COLUMNS] for row in rows),
        key=lambda values: str(values[0]),
    )
    for values in normalized:
        encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        digest.update(encoded.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def archive_month(occurred_at: str) -> str:
    normalized = normalize_timestamp(occurred_at)
    if normalized is None:
        raise ValueError(f"task event has invalid occurred_at: {occurred_at!r}")
    return normalized[:7]


class TaskEventArchiveStore:
    def __init__(self, base_dir: Path | str = Path(".")) -> None:
        self.base_dir = Path(base_dir)
        self.db_path = default_database_path(self.base_dir)
        self.archive_dir = resolve_data_dir(self.base_dir) / "archive" / "task_events"

    def select_eligible(
        self,
        *,
        now: datetime,
        limit: int,
        after_rowid: int = 0,
        expired: BudgetCheck | None = None,
    ) -> list[dict[str, Any]]:
        _check_budget(expired)
        critical_cutoff, regular_cutoff = _cutoffs(now)
        columns = ", ".join(f"e.{column}" for column in EVENT_COLUMNS)
        with connect_readonly(self.db_path) as conn:
            _bound_sql(conn, expired)
            rows = conn.execute(
                f"""
                select e.rowid as archive_source_rowid, {columns}
                from task_events e
                where e.rowid > ? and {_eligibility_sql()}
                order by e.rowid
                limit ?
                """,
                (max(0, int(after_rowid)), critical_cutoff, regular_cutoff, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_eligible(self, *, now: datetime, expired: BudgetCheck | None = None) -> int | None:
        """Return an indexed upper-bound estimate without scanning every old event."""
        _, regular_cutoff = _cutoffs(now)
        if expired is not None and expired():
            return None
        with connect_readonly(self.db_path) as conn:
            _bound_sql(conn, expired)
            try:
                row = conn.execute(
                    "select count(*) from task_events where occurred_at < ?",
                    (regular_cutoff,),
                ).fetchone()
            except sqlite3.OperationalError:
                if expired is not None and expired():
                    return None
                raise
        return int(row[0])

    def archive_rows(self, rows: Sequence[dict[str, Any]], *, archived_at: str, expired: BudgetCheck | None = None) -> list[Path]:
        """Commit rows to each monthly archive before the caller touches the hot DB."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(archive_month(str(row["occurred_at"])), []).append(row)
        paths: list[Path] = []
        for month, month_rows in sorted(grouped.items()):
            _check_budget(expired)
            path = self._archive_path(month)
            conn = self._open_archive(path, writable=True)
            try:
                _bound_sql(conn, expired)
                conn.executemany(
                    f"""
                    insert or ignore into task_events ({', '.join(EVENT_COLUMNS)}, archived_at)
                    values ({', '.join('?' for _ in range(len(EVENT_COLUMNS) + 1))})
                    """,
                    [tuple(row[column] for column in EVENT_COLUMNS) + (archived_at,) for row in month_rows],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            os.chmod(path, 0o600)
            paths.append(path)
        return paths

    def verify_rows(self, rows: Sequence[dict[str, Any]], *, expired: BudgetCheck | None = None) -> None:
        """Require exact row count and all-column checksum in every monthly archive."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(archive_month(str(row["occurred_at"])), []).append(row)
        for month, expected in grouped.items():
            _check_budget(expired)
            path = self._archive_path(month)
            conn = self._open_archive(path, writable=False)
            try:
                _bound_sql(conn, expired)
                actual: list[dict[str, Any]] = []
                for chunk in _chunks([str(row["event_id"]) for row in expected], 500):
                    placeholders = ",".join("?" for _ in chunk)
                    fetched = conn.execute(
                        f"select {', '.join(EVENT_COLUMNS)} from task_events where event_id in ({placeholders})",
                        tuple(chunk),
                    ).fetchall()
                    actual.extend(dict(row) for row in fetched)
            finally:
                conn.close()
            if len(actual) != len(expected) or stable_event_checksum(actual) != stable_event_checksum(expected):
                raise RuntimeError(f"task event archive verification failed for {path.name}")

    def delete_verified(self, rows: Sequence[dict[str, Any]], *, now: datetime, expired: BudgetCheck | None = None) -> tuple[int, float]:
        """Delete only rows that remain eligible, in one short hot-DB transaction."""
        if not rows:
            return 0, 0.0
        _check_budget(expired)
        critical_cutoff, regular_cutoff = _cutoffs(now)
        started = time.monotonic()
        with connect_existing(self.db_path) as conn:
            _bound_sql(conn, expired)
            conn.execute("begin immediate")
            try:
                conn.execute("create temp table if not exists archive_delete_ids(event_id text primary key)")
                conn.execute("delete from archive_delete_ids")
                conn.executemany(
                    "insert into archive_delete_ids(event_id) values (?)",
                    ((str(row["event_id"]),) for row in rows),
                )
                cursor = conn.execute(
                    f"delete from task_events as e where {_eligibility_sql(id_filter=True)}",
                    (critical_cutoff, regular_cutoff),
                )
                deleted = int(cursor.rowcount)
                conn.execute("commit")
            except Exception:
                conn.execute("rollback")
                raise
        return deleted, time.monotonic() - started

    def history_stats(self, *, now: datetime, expired: BudgetCheck | None = None) -> dict[str, Any]:
        since = _iso(_utc(now) - timedelta(hours=24))
        recent_lower_bound = _iso(_utc(now) - timedelta(hours=48))
        with connect_readonly(self.db_path) as conn:
            _bound_sql(conn, expired)
            tasks = _optional_scalar(conn, "select count(*) from tasks", (), expired)
            events = _optional_scalar(conn, "select count(*) from task_events", (), expired)
            new_tasks = _optional_scalar(conn, "select count(*) from tasks where created_at >= ?", (since,), expired)
            new_events = _optional_scalar(conn, "select count(*) from task_events where occurred_at >= ? and source_timestamp(occurred_at) >= ?", (recent_lower_bound, since), expired)
            page_count = _optional_scalar(conn, "pragma page_count", (), expired)
            freelist_count = _optional_scalar(conn, "pragma freelist_count", (), expired)
        archive_bytes: int | None = 0
        if self.archive_dir.exists():
            for path in self.archive_dir.glob("*.sqlite3"):
                if expired is not None and expired():
                    archive_bytes = None
                    break
                archive_bytes += path.stat().st_size
        return {
            "tasks_rows": tasks,
            "task_events_rows": events,
            "main_db_size_bytes": self.db_path.stat().st_size if expired is None or not expired() else None,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "freelist_ratio": (freelist_count / page_count) if page_count and freelist_count is not None else None,
            "archive_size_bytes": archive_bytes,
            "tasks_added_24h": new_tasks,
            "events_added_24h": new_events,
        }

    def claim_daily_stats_log(self, *, now: datetime, expired: BudgetCheck | None = None) -> bool:
        """Persist the daily-log claim in the archive's single settings JSON value."""
        if expired is not None and expired():
            return False
        day = _utc(now).date().isoformat()
        occurred_at = _iso(now)
        with connect_existing(self.db_path) as conn:
            _bound_sql(conn, expired)
            conn.execute("begin immediate")
            try:
                row = conn.execute(
                    "select value from settings where key = ?", (TASK_EVENT_ARCHIVE_PROGRESS_KEY,)
                ).fetchone()
                progress = json.loads(row[0]) if row else {}
                if progress.get("last_stats_log_date") == day:
                    conn.execute("rollback")
                    return False
                progress["last_stats_log_date"] = day
                conn.execute(
                    """
                    insert into settings(key, value, updated_at) values (?, ?, ?)
                    on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (TASK_EVENT_ARCHIVE_PROGRESS_KEY, json.dumps(progress, sort_keys=True), occurred_at),
                )
                conn.execute("commit")
            except Exception:
                conn.execute("rollback")
                raise
        return True

    def query(self, *, task_id: str | None = None, chain_id: str | None = None) -> list[dict[str, Any]]:
        if (task_id is None) == (chain_id is None):
            raise ValueError("exactly one of task_id or chain_id is required")
        column, value = ("task_id", task_id) if task_id is not None else ("chain_id", chain_id)
        merged: dict[str, dict[str, Any]] = {}
        if self.db_path.exists():
            with connect_readonly(self.db_path) as conn:
                for row in conn.execute(
                    f"select {', '.join(EVENT_COLUMNS)} from task_events where {column} = ?",
                    (value,),
                ).fetchall():
                    merged[str(row["event_id"])] = dict(row) | {
                        "storage": "hot",
                        "archived_at": None,
                    }
        if self.archive_dir.exists():
            for path in sorted(self.archive_dir.glob("task_events-????-??.sqlite3")):
                conn = self._open_archive(path, writable=False)
                try:
                    rows = conn.execute(
                        f"select {', '.join(EVENT_COLUMNS)}, archived_at from task_events where {column} = ?",
                        (value,),
                    ).fetchall()
                finally:
                    conn.close()
                for row in rows:
                    merged.setdefault(
                        str(row["event_id"]),
                        dict(row) | {"storage": "archive"},
                    )
        result = sorted(merged.values(), key=lambda row: (str(row["occurred_at"]), str(row["event_id"])))
        for row in result:
            try:
                row["payload"] = json.loads(str(row["payload"]))
            except (TypeError, ValueError):
                pass
        return result

    def _archive_path(self, month: str) -> Path:
        return self.archive_dir / f"task_events-{month}.sqlite3"

    def _open_archive(self, path: Path, *, writable: bool) -> sqlite3.Connection:
        if writable:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.archive_dir.parent, 0o700)
            os.chmod(self.archive_dir, 0o700)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            conn = sqlite3.connect(path, timeout=30)
            os.chmod(path, 0o600)
            conn.execute("pragma journal_mode = delete")
            conn.execute("pragma synchronous = full")
            conn.executescript(
                """
                create table if not exists task_events (
                  event_id text primary key,
                  task_id text,
                  chain_id text,
                  event_type text not null,
                  status text,
                  failure_class text,
                  payload text not null default '{}',
                  occurred_at text not null,
                  archived_at text not null
                );
                create index if not exists idx_task_events_task on task_events(task_id);
                create index if not exists idx_task_events_chain on task_events(chain_id);
                create index if not exists idx_task_events_occurred_at on task_events(occurred_at);
                """
            )
            conn.row_factory = sqlite3.Row
            return conn
        resolved = path.resolve()
        conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma query_only = on")
        return conn


def _chunks(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]
