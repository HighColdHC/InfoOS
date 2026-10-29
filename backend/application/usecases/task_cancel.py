"""Cancel unowned durable work without executing terminal-stage transitions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.domain.timestamps import utc_now_seconds_z
from backend.infrastructure.storage.database import connect_writable
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.tasks.cancellation import cancel_to_connection
from backend.infrastructure.storage.tasks.operations import task_row


def cancel_task(base_dir: Path | str, task_id: str, *, reason: str) -> dict[str, Any]:
    conn = connect_writable(resolve_sqlite_path(base_dir))
    try:
        # Serialize with worker claims and retries before reading eligibility.
        conn.execute("begin immediate")
        task = task_row(conn, task_id)
        if task is None:
            conn.rollback()
            return _error(task_id, "task_not_found", 404)
        if task["status"] not in {"pending", "blocked"}:
            conn.rollback()
            return _error(task_id, "task_not_cancellable", 409)
        now = utc_now_seconds_z()
        artifacts = json.loads(task["artifacts_summary"])
        cancel_to_connection(conn, task_id, reason=reason, occurred_at=now)
        TaskEventRepository.append_to_connection(
            conn, task_id=task_id, chain_id=artifacts.get("chain_id"),
            event_type="cancelled", status="skipped", occurred_at=now,
            payload={
                "reason": reason, "trigger": "manual", "previous_status": task["status"],
                "previous_error_reason": task["error_reason"],
                "previous_failure_class": task["last_failure_class"],
                "attempt": task["attempt"],
            },
        )
        conn.commit()
        return {"ok": True, "task_id": task_id, "status": "skipped", "reason": reason}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _error(task_id: str, code: str, status_code: int) -> dict[str, Any]:
    return {"ok": False, "task_id": task_id, "code": code, "status_code": status_code}
