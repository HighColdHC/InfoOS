"""Atomic failed Webhook replay selection and generation rebinding."""

from __future__ import annotations

from typing import Any

from backend.infrastructure.storage.base import InfoOSRepository


class WebhookReplayStore(InfoOSRepository):
    def replay(
        self,
        *,
        token_id: str,
        scheduled_at: str,
        limit: int,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute("begin immediate")
            token = conn.execute(
                "select * from api_tokens where token_id = ?",
                (token_id,),
            ).fetchone()
            if token is None:
                return {"state": "missing", "task_ids": []}
            if token["revoked_at"] or token["replaced_by_token_id"]:
                return {"state": "revoked", "task_ids": []}
            if token["webhook_delivery_status"] != "active":
                return {"state": "inactive", "task_ids": []}
            generation = int(token["webhook_config_generation"] or 1)
            rows = conn.execute(
                """select task_id from tasks
                   where task_type = 'webhook_deliver'
                     and status in ('failed', 'blocked')
                     and json_extract(artifacts_summary, '$.token_id') = ?
                   order by created_at desc, task_id limit ?""",
                (token_id, max(1, min(int(limit), 100))),
            ).fetchall()
            replayed: list[str] = []
            for row in rows:
                cursor = conn.execute(
                    """update tasks set status = 'pending', attempt = 1,
                         next_run_at = ?, error_reason = null, claimed_by = null,
                         lease_expires_at = null, heartbeat_at = null,
                         completion_token = null,
                         artifacts_summary = json_set(
                           artifacts_summary, '$.webhook_config_generation', ?
                         ), updated_at = ?
                       where task_id = ? and status in ('failed', 'blocked')""",
                    (scheduled_at, generation, scheduled_at, row["task_id"]),
                )
                if cursor.rowcount:
                    replayed.append(str(row["task_id"]))
            return {"state": "active", "task_ids": replayed}
