"""SQLite-backed exclusive runtime resource leases."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from backend.infrastructure.storage.database import connect_writable
from backend.infrastructure.storage.paths import resolve_sqlite_path


class RuntimeResourceLeaseRepository:
    def __init__(self, base_dir: str | Path):
        self.db_path = resolve_sqlite_path(base_dir)

    @staticmethod
    def acquire_on_connection(
        conn: sqlite3.Connection,
        resource_kind: str,
        resource_key: str,
        *,
        owner_token: str,
        task_id: str,
        acquired_at: str,
        lease_expires_at: str,
    ) -> bool:
        conn.execute(
            """
            delete from runtime_resource_leases
            where resource_kind = ? and resource_key = ? and lease_expires_at <= ?
            """,
            (resource_kind, resource_key, acquired_at),
        )
        cursor = conn.execute(
            """
            insert or ignore into runtime_resource_leases (
              resource_kind, resource_key, owner_token, task_id,
              acquired_at, lease_expires_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resource_kind,
                resource_key,
                owner_token,
                task_id,
                acquired_at,
                lease_expires_at,
                acquired_at,
            ),
        )
        return cursor.rowcount > 0

    def acquire(
        self,
        resource_kind: str,
        resource_key: str,
        *,
        owner_token: str,
        task_id: str,
        acquired_at: str,
        lease_expires_at: str,
    ) -> bool:
        with self._connect() as conn:
            return self.acquire_on_connection(
                conn,
                resource_kind,
                resource_key,
                owner_token=owner_token,
                task_id=task_id,
                acquired_at=acquired_at,
                lease_expires_at=lease_expires_at,
            )

    def renew(
        self,
        resource_kind: str,
        resource_key: str,
        *,
        owner_token: str,
        updated_at: str,
        lease_expires_at: str,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                update runtime_resource_leases
                set lease_expires_at = ?, updated_at = ?
                where resource_kind = ? and resource_key = ? and owner_token = ?
                """,
                (
                    lease_expires_at,
                    updated_at,
                    resource_kind,
                    resource_key,
                    owner_token,
                ),
            )
        return cursor.rowcount > 0

    def release(
        self,
        resource_kind: str,
        resource_key: str,
        *,
        owner_token: str,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                delete from runtime_resource_leases
                where resource_kind = ? and resource_key = ? and owner_token = ?
                """,
                (resource_kind, resource_key, owner_token),
            )
        return cursor.rowcount > 0

    @contextmanager
    def _connect(self):
        conn = connect_writable(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
