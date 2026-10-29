"""Bounded repair of capture terminal states."""
from __future__ import annotations

from typing import Any

from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository


class AuditRepairRepository(InfoOSRepository):
    def plan(self, *, limit: int = 2000) -> dict[str, list[dict[str, Any]]]:
        with self.connect_existing() as conn:
            captures = [dict(r) for r in conn.execute(
                """select c.capture_id,c.run_id,c.task_id,t.status,t.error_reason,t.last_failure_class,
                       coalesce(t.finished_at,t.updated_at) finished_at
                   from capture_receipts c join run_history r on r.run_id=c.run_id
                   join tasks t on t.task_id=c.task_id
                   where r.status in ('pending','running') and t.status in ('failed','blocked','skipped')
                   order by c.created_at limit ?""", (limit,))]
        return {"capture_terminals": captures}

    def apply(self, plan: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        changed_captures = 0
        with self.connect() as conn:
            conn.execute("begin immediate")
            for row in plan["capture_terminals"]:
                if conn.execute("select 1 from tasks where task_id=? and status=?", (row["task_id"],row["status"])).fetchone() is None:
                    continue
                CaptureReceiptRepository.finalize_root_to_connection(
                    conn, row["task_id"], status=row["status"],
                    error_code=row["last_failure_class"] or "capture_processing_failed",
                    finished_at=row["finished_at"],
                )
                conn.execute(
                    """update tasks set artifacts_summary=json_set(artifacts_summary,
                       '$.capture_id',?,'$.run_id',?) where task_id=?""",
                    (row["capture_id"], row["run_id"], row["task_id"]),
                )
                changed_captures += 1
        return {"capture_terminals_updated": changed_captures}
