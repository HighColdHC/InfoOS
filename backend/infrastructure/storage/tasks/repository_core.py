"""Connection ownership and expired-lease recovery for durable tasks."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from backend.infrastructure.storage.tasks.admission import ResourceAdmissionMixin
from backend.infrastructure.storage.tasks.claims import TaskClaimMixin, _row_to_task
from backend.infrastructure.storage.tasks.finalization import TaskFinalizationMixin, TaskFinalizationQueue
from backend.infrastructure.storage.tasks.recovery import ResourceRecoveryMixin
from backend.domain.task_lease_recovery import lease_recovery_next_run_at
from backend.domain.tasks import Task, TaskStatus
from backend.infrastructure.storage.tasks.repository_maintenance import TaskMaintenanceMixin
from backend.infrastructure.storage.tasks.repository_mutations import TaskMutationMixin
from backend.infrastructure.storage.tasks.repository_queries import TaskQueryMixin
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.database import connect_existing, connect_writable


class TaskRepository(
    ResourceAdmissionMixin,
    ResourceRecoveryMixin,
    TaskClaimMixin,
    TaskFinalizationMixin,
    TaskMutationMixin,
    TaskQueryMixin,
    TaskMaintenanceMixin,
):

    def __init__(self, db_path: str, *, base_dir: str | Path | None = None):
        self.db_path = db_path
        self.base_dir = Path(base_dir) if base_dir is not None else None


    @classmethod
    def for_base_dir(cls, base_dir: str | Path) -> "TaskRepository":
        from backend.infrastructure.storage.paths import resolve_sqlite_path

        db_path = resolve_sqlite_path(base_dir)
        return cls(str(db_path), base_dir=base_dir)


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


    def _connect_existing(self):
        """Open the task database for a passive read without creating it."""
        return connect_existing(self.db_path)


    def task_ids_for_card(self, card_id: str) -> set[str]:
        """All task identities for one card and its assets, without history truncation."""
        with self._connect_existing() as conn:
            rows = conn.execute(
                """
                select task_id from tasks
                where target_kind = 'card' and target_id = ?
                union all
                select t.task_id
                from card_assets a indexed by idx_card_assets_card
                join tasks t indexed by idx_tasks_target
                  on t.target_kind = 'asset' and t.target_id = a.asset_id
                where a.card_id = ?
                """,
                (card_id, card_id),
            ).fetchall()
        return {str(row["task_id"]) for row in rows}


    def _connect_for_claim(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 30000")
        conn.execute("pragma foreign_keys = on")
        conn.execute("pragma synchronous = normal")
        for attempt in range(20):
            try:
                conn.execute("pragma journal_mode = wal")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 19:
                    conn.close()
                    raise
                time.sleep(0.01)
        return conn


    def recover_expired_leases(
        self, now: str, *, event_occurred_at: str | None = None,
        base_dir: str | Path | None = None,
        enqueue_downstream: Callable[[TaskFinalizationQueue, Task, Path, str], list[str]] | None = None,
    ) -> int:
        """Count expired executions, retry with backoff, or finalize at the cap."""
        with self._connect_existing() as read_conn:
            if read_conn.execute(
                """select 1 from tasks where status = ? and lease_expires_at is not null
                   and lease_expires_at < ? limit 1""",
                (TaskStatus.RUNNING.value, now),
            ).fetchone() is None:
                return 0
            expired = read_conn.execute(
                """
                select * from tasks
                where status = ? and lease_expires_at is not null and lease_expires_at < ?
                order by lease_expires_at, task_id
                """,
                (TaskStatus.RUNNING.value, now),
            ).fetchall()
        recovered = 0
        downstream_base_dir = Path(base_dir) if base_dir is not None else self.base_dir
        for row in expired:
            task = _row_to_task(row)
            attempt = task.attempt + 1
            exhausted = task.attempt >= task.max_attempts
            if exhausted:
                if downstream_base_dir is None:
                    raise ValueError("base_dir is required to finalize an expired task lease")
                if enqueue_downstream is None:
                    raise ValueError("enqueue_downstream is required to finalize an expired task lease")
                def record_recovery(conn: sqlite3.Connection, *, recovered_task: Task = task, new_attempt: int = attempt) -> None:
                    if event_occurred_at is not None:
                        TaskEventRepository.append_to_connection(
                            conn, event_type="lease_recovered", task_id=recovered_task.task_id,
                            chain_id=recovered_task.artifacts_summary.get("chain_id"),
                            status=TaskStatus.FAILED.value,
                            payload={"attempt": new_attempt, "exhausted": True},
                            occurred_at=event_occurred_at,
                        )

                finalized = self.finalize_terminal_with_downstream(
                    task.task_id, worker_id=task.claimed_by or "",
                    status=TaskStatus.FAILED, artifacts=dict(task.artifacts_summary),
                    error_reason="lease_expired_attempts_exhausted", result_version=None,
                    finished_at=now, attempt=attempt, failure_class="lease_expired",
                    clear_next_run_at=True, preserve_result_metadata=True,
                    enqueue_downstream=lambda queue, recovered_task=task: enqueue_downstream(
                        queue, recovered_task, downstream_base_dir, now,
                    ),
                    event_chain_id=task.artifacts_summary.get("chain_id"),
                    event_payload={"attempt": attempt, "exhausted": True},
                    event_failure_class="lease_expired",
                    transaction_callback=record_recovery,
                    lease_expired_before=now,
                )
                recovered += finalized is not None
                continue
            conn = self._connect_for_claim()
            try:
                conn.execute("begin immediate")
                cursor = conn.execute(
                    """
                    update tasks set
                        status = ?, attempt = ?, next_run_at = ?, error_reason = ?,
                        claimed_by = null, lease_expires_at = null, heartbeat_at = null,
                        completion_token = null, updated_at = ?
                    where task_id = ? and status = ? and claimed_by = ?
                      and attempt = ? and lease_expires_at is not null and lease_expires_at < ?
                    """,
                    (
                        TaskStatus.PENDING.value,
                        attempt,
                        lease_recovery_next_run_at(now, attempt),
                        "recovered_expired_lease",
                        now,
                        task.task_id,
                        TaskStatus.RUNNING.value,
                        task.claimed_by,
                        task.attempt,
                        now,
                    ),
                )
                if cursor.rowcount and event_occurred_at is not None:
                    TaskEventRepository.append_to_connection(
                        conn,
                        event_type="lease_recovered",
                        task_id=task.task_id,
                        chain_id=task.artifacts_summary.get("chain_id"),
                        status=TaskStatus.PENDING.value,
                        payload={"attempt": attempt, "exhausted": False},
                        occurred_at=event_occurred_at,
                    )
                conn.execute("commit")
                recovered += bool(cursor.rowcount)
            except Exception:
                if conn.in_transaction:
                    conn.execute("rollback")
                raise
            finally:
                conn.close()
        return recovered
