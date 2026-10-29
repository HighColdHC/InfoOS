"""Durable, compact task and acquisition lifecycle events."""
from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now


class TaskEventRepository(InfoOSRepository):
    """Append-only operational events that survive worker restarts."""

    def append(
        self,
        *,
        event_type: str,
        task_id: str | None = None,
        chain_id: str | None = None,
        status: str | None = None,
        failure_class: str | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        if conn is not None:
            return self.append_to_connection(
                conn,
                event_type=event_type,
                task_id=task_id,
                chain_id=chain_id,
                status=status,
                failure_class=failure_class,
                payload=payload,
                occurred_at=occurred_at,
            )
        with self.connect() as own_conn:
            return self.append_to_connection(
                own_conn,
                event_type=event_type,
                task_id=task_id,
                chain_id=chain_id,
                status=status,
                failure_class=failure_class,
                payload=payload,
                occurred_at=occurred_at,
            )

    @staticmethod
    def append_to_connection(
        conn: sqlite3.Connection,
        *,
        event_type: str,
        task_id: str | None = None,
        chain_id: str | None = None,
        status: str | None = None,
        failure_class: str | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> str:
        """Append through a caller-owned transaction without committing it."""
        event_id = f"evt_{uuid4().hex}"
        conn.execute(
            """
            insert into task_events (
              event_id, task_id, chain_id, event_type, status,
              failure_class, payload, occurred_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                chain_id,
                event_type,
                status,
                failure_class,
                dumps(payload or {}),
                occurred_at or utc_now(),
            ),
        )
        return event_id

    def list_events(
        self,
        *,
        task_id: str | None = None,
        chain_id: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("task_id", task_id),
            ("chain_id", chain_id),
            ("event_type", event_type),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if since is not None:
            clauses.append("occurred_at >= ?")
            params.append(since)
        params.append(max(1, min(int(limit), 10000)))
        where = " where " + " and ".join(clauses) if clauses else ""
        with self.connect_existing() as conn:
            rows = conn.execute(
                "select * from task_events" + where
                + " order by occurred_at, rowid limit ?",
                tuple(params),
            ).fetchall()
        return [
            dict(row) | {"payload": loads(row["payload"], {})}
            for row in rows
        ]
