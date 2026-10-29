"""Stable task pages used by pipeline reconciliation."""

from __future__ import annotations

import json
from typing import Any

from backend.infrastructure.storage.base import InfoOSRepository


class ReconciliationTaskQueries(InfoOSRepository):
    def max_parent_task_id(self) -> str:
        with self.connect_existing() as conn:
            row = conn.execute(
                """select max(task_id) as task_id from tasks
                   where json_array_length(
                     json_extract(artifacts_summary, '$.downstream_task_ids')
                   ) > 0"""
            ).fetchone()
        return str(row["task_id"] or "") if row is not None else ""

    def list_parent_page(
        self,
        *,
        after_task_id: str,
        upper_task_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not upper_task_id or limit <= 0:
            return []
        with self.connect_existing() as conn:
            rows = conn.execute(
                """select task_id, artifacts_summary from tasks
                   where task_id > ? and task_id <= ?
                     and json_array_length(
                       json_extract(artifacts_summary, '$.downstream_task_ids')
                     ) > 0
                   order by task_id limit ?""",
                (after_task_id, upper_task_id, int(limit)),
            ).fetchall()
        return [
            {
                "task_id": str(row["task_id"]),
                "artifacts_summary": json.loads(row["artifacts_summary"]),
            }
            for row in rows
        ]
