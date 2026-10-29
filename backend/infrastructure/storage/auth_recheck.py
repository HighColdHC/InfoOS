"""Auth recheck evidence queries, using existing task events and resource leases."""
from __future__ import annotations

from backend.domain.task_resources import ResourceLease
from backend.infrastructure.storage.base import InfoOSRepository


class AuthRecheckRepository(InfoOSRepository):
    def recently_verified(self, platform: str, *, since: str, now: str) -> bool:
        with self.connect_existing() as conn:
            return conn.execute(
                """select 1 from task_events
                where event_type in ('retry_scheduled', 'failed', 'blocked')
                  and occurred_at >= ? and occurred_at <= ?
                  and json_extract(payload, '$.auth_recheck.platform') = ?
                  and json_extract(payload, '$.auth_recheck.outcome') = 'verified'
                limit 1""",
                (since, now, platform),
            ).fetchone() is not None

    def owns_browser_resource(
        self, task_id: str, worker_id: str, resource: ResourceLease, *, now: str,
    ) -> bool:
        with self.connect_existing() as conn:
            return conn.execute(
                """select 1 from tasks task join runtime_resource_leases lease
                  on lease.task_id = task.task_id
                where task.task_id = ? and task.status = 'running'
                  and task.claimed_by = ? and task.lease_expires_at > ?
                  and lease.resource_kind = 'browser_site'
                  and lease.resource_key = ? and lease.owner_token = ?
                  and lease.lease_expires_at > ?""",
                (task_id, worker_id, now, resource.resource_key, resource.owner_token, now),
            ).fetchone() is not None
