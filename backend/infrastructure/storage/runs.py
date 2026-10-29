"""Run repository.

runs — module migrated to infoos in PR 4.21.
The corresponding infoos/storage/runs.py was deleted in this commit.
This file is now the sole source of truth.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads


class RunRepository(InfoOSRepository):
    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                "select * from run_history where run_id = ?",
                (run_id,),
            ).fetchone()
        return self._row_dicts([row])[0] if row else None

    def append_run(self, payload: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self.append_to_connection(conn, payload)
            return
        with self.connect() as conn:
            self.append_to_connection(conn, payload)

    @staticmethod
    def append_to_connection(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
        """Insert one run using the caller-owned acquisition transaction."""
        conn.execute(
                """
                insert into run_history (
                  run_id, source_id, connector_id, scheduled_for, started_at, finished_at,
                  status, fetched_count, visible_count, new_count, skipped_count,
                  dedup_skipped_count, error_count, duration_ms,
                  next_run_at, attempt, trigger, decision, error_summary, raw_payload,
                  task_type, target_kind, target_id, artifacts_summary, derived_task_ids
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["run_id"],
                    payload["source_id"],
                    payload.get("connector_id"),
                    payload["scheduled_for"],
                    payload["started_at"],
                    payload["finished_at"],
                    payload["status"],
                    int(payload.get("fetched_count", 0)),
                    int(payload.get("visible_count", 0)),
                    int(payload.get("new_count", 0)),
                    int(payload.get("skipped_count", 0)),
                    int(payload.get("dedup_skipped_count", 0)),
                    int(payload.get("error_count", 0)),
                    int(payload.get("duration_ms", 0)),
                    payload.get("next_run_at"),
                    int(payload.get("attempt", 1)),
                    payload.get("trigger", "scheduled"),
                    payload.get("decision"),
                    payload.get("error_summary"),
                    dumps(payload.get("raw_payload", {})),
                    payload.get("task_type", "fetch"),
                    payload.get("target_kind"),
                    payload.get("target_id"),
                    dumps(payload.get("artifacts_summary", {})),
                    dumps(payload.get("derived_task_ids", [])),
                ),
        )

    def list_runs(self, *, source_id: str | None = None,
                  task_type: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if task_type:
            clauses.append("task_type = ?")
            params.append(task_type)
        sql = "select * from run_history"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by finished_at desc"
        if limit is not None:
            sql += " limit ?"
            params.append(max(0, int(limit)))
        with self.connect_existing() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return self._row_dicts(rows)

    def list_by_target(self, *, target_kind: str, target_id: str,
                       task_type: str | None = None) -> list[dict[str, Any]]:
        """Find runs by target (e.g. all run_history rows for a card_id)."""
        clauses = ["target_kind = ?", "target_id = ?"]
        params: list[Any] = [target_kind, target_id]
        if task_type:
            clauses.append("task_type = ?")
            params.append(task_type)
        sql = "select * from run_history where " + " and ".join(clauses) + \
              " order by finished_at desc"
        with self.connect_existing() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return self._row_dicts(rows)

    def _row_dicts(self, rows: Iterable[Any]) -> list[dict[str, Any]]:
        runs = []
        for row in rows:
            run = dict(row)
            run["raw_payload"] = loads(run["raw_payload"], {})
            run["artifacts_summary"] = loads(run["artifacts_summary"], {})
            run["derived_task_ids"] = loads(run["derived_task_ids"], [])
            run["connector_diagnostic"] = run["raw_payload"].get("connector_diagnostic")
            runs.append(run)
        return runs
