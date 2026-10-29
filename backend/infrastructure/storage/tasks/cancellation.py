"""Task cancellation writes within the use case's transaction."""
from __future__ import annotations

import json
import sqlite3


def cancel_to_connection(
    conn: sqlite3.Connection, task_id: str, *, reason: str, occurred_at: str,
) -> None:
    """The caller holds the write lock and has checked the current status."""
    conn.execute(
        """update tasks set status = 'skipped', error_reason = 'operator_cancelled',
            artifacts_summary = json_set(artifacts_summary, '$.cancellation', json(?)),
            next_run_at = null, claimed_by = null, lease_expires_at = null,
            heartbeat_at = null, completion_token = null, finished_at = ?, updated_at = ?
        where task_id = ?""",
        (json.dumps({"reason": reason}, ensure_ascii=False), occurred_at, occurred_at, task_id),
    )
