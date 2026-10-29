"""SQLite connection and readiness helpers for InfoOS."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import sqlite3
from threading import Lock, get_ident
from typing import Iterator

from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.storage.migrations.runner import (
    LATEST_VERSION,
    DatabaseNotReady,
    DatabaseVersionTooNew,
)
from backend.infrastructure.storage.paths import resolve_sqlite_path


def default_database_path(base_dir: Path | str | None = None) -> Path:
    """Return the canonical path to infoos.sqlite3."""
    return resolve_sqlite_path(base_dir)


@contextmanager
def connect(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma busy_timeout = 5000")
    conn.execute("pragma synchronous = normal")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def connect_writable(db_path: Path | str) -> sqlite3.Connection:
    """Connect with write-friendly settings."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode = wal")
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma busy_timeout = 10000")
    conn.execute("pragma synchronous = normal")
    return conn


_READ_CONNECTION: ContextVar[tuple[Path, sqlite3.Connection, int] | None] = ContextVar(
    "infoos_read_connection", default=None,
)


def _connection_path(db_path: Path | str) -> Path:
    path = Path(db_path)
    # Repositories already resolve their path at construction.
    return path if path.is_absolute() else path.resolve()


def _shared_connection(path: Path) -> sqlite3.Connection | None:
    current = _READ_CONNECTION.get()
    if current is not None and current[2] == get_ident():
        if path == current[0] or path.resolve() == current[0]:
            return current[1]
    return None


@contextmanager
def _borrow_read_connection(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    in_transaction = conn.in_transaction
    try:
        yield conn
    finally:
        # Closing an owned connection used to end any locally opened read
        # transaction. Preserve that boundary without closing a borrowed one.
        if not in_transaction and conn.in_transaction:
            conn.rollback()


@contextmanager
def read_connection_scope(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Own one thread-local read connection; nested same-database scopes borrow it."""
    path = Path(db_path).resolve()
    shared = _shared_connection(path)
    if shared is not None:
        yield shared
        return
    with connect_readonly(path) as conn:
        token = _READ_CONNECTION.set((path, conn, get_ident()))
        try:
            yield conn
        finally:
            _READ_CONNECTION.reset(token)


@contextmanager
def connect_existing(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open an existing database without creating or configuring storage."""
    path = _connection_path(db_path)
    shared = _shared_connection(path)
    if shared is not None:
        with _borrow_read_connection(shared) as conn:
            yield conn
        return
    conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.create_function("source_timestamp", 1, normalize_timestamp, deterministic=True)
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma busy_timeout = 5000")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def connect_readonly(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    path = _connection_path(db_path)
    shared = _shared_connection(path)
    if shared is not None:
        with _borrow_read_connection(shared) as conn:
            yield conn
        return
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma query_only = on")
    conn.execute("pragma foreign_keys = on")
    conn.create_function("source_timestamp", 1, normalize_timestamp, deterministic=True)
    try:
        yield conn
    finally:
        conn.close()


_READY_DATABASES: dict[Path, tuple[tuple[int, int, int, int], tuple[int, int, int, int] | None]] = {}
_READY_DATABASES_LOCK = Lock()


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


def _readiness_signature(path: Path) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int] | None]:
    main = _file_signature(path)
    wal_path = Path(f"{path}-wal")
    try:
        wal = _file_signature(wal_path)
    except FileNotFoundError:
        wal = None
    return main, wal


def ensure_database_initialized(db_path: Path | str) -> Path:
    """Assert an existing database completed the explicit startup migration."""
    path = Path(db_path).expanduser().resolve()
    with _READY_DATABASES_LOCK:
        try:
            signature = _readiness_signature(path)
        except FileNotFoundError as exc:
            _READY_DATABASES.pop(path, None)
            raise DatabaseNotReady(path, None) from exc
        if _READY_DATABASES.get(path) == signature:
            return path
        try:
            with connect_readonly(path) as conn:
                version = int(conn.execute("pragma user_version").fetchone()[0])
        except sqlite3.Error as exc:
            raise DatabaseNotReady(path, None) from exc
        if version > LATEST_VERSION:
            raise DatabaseVersionTooNew(path, version)
        if version != LATEST_VERSION:
            raise DatabaseNotReady(path, version)
        _READY_DATABASES[path] = _readiness_signature(path)
        return path
