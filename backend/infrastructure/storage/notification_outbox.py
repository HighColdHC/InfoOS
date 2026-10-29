"""Outbox persistence; enqueue and durable task creation share one transaction."""
from uuid import uuid4

from backend.domain.notifications import NotificationKind
from backend.domain.tasks import Task, TaskType
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.tasks.finalization import enqueue_task


class NotificationOutboxRepository(InfoOSRepository):
    def claim_command(self, message_id: str, *, now: str, expires_at: str) -> str | None:
        """Persist an at-most-once receipt, including commands with no text reply."""
        notification_id = uuid4().hex
        with self.connect() as conn:
            changed = conn.execute("""insert into notification_outbox
                (notification_id,kind,channel,dedupe_key,status,title,body,
                 next_attempt_at,expires_at,created_at,updated_at,quiet_allowed)
                values (?,?,'feishu',?,'skipped','','',?,?,?,?,1)
                on conflict(dedupe_key) do nothing""",
                (notification_id, NotificationKind.COMMAND_REPLY.value, f'feishu-cmd:{message_id}:feishu',
                 now, expires_at, now, now)).rowcount
        return notification_id if changed else None

    def reply_command(self, notification_id: str, *, title: str, body: str, now: str) -> None:
        with self.connect() as conn:
            conn.execute('begin immediate')
            conn.execute("""update notification_outbox set status='pending',title=?,body=?,
                next_attempt_at=?,updated_at=? where notification_id=?""", (title, body, now, now, notification_id))
            enqueue_task(conn, Task(task_id=f'notification_{notification_id}', task_type=TaskType.NOTIFICATION_DELIVER,
                target_kind='notification', target_id=notification_id, next_run_at=now), now=now)

    def enqueue(self, *, kind: str, channel: str, dedupe_key: str, title: str, body: str,
                image_path: str | None, related: tuple[str, str] | None, now: str,
                next_attempt_at: str, expires_at: str, quiet_allowed: bool) -> str | None:
        with self.connect() as conn:
            conn.execute("begin immediate")
            if related is not None and related[0] == 'auth_incident' and kind in ('auth_recovery_qr', 'auth_recovery_text'):
                actionable = conn.execute("select 1 from auth_recovery_incidents where incident_id=? and status!='resolved'", (related[1],)).fetchone()
                if actionable is None:
                    return None
            notification_id = uuid4().hex
            conn.execute("""insert into notification_outbox
                (notification_id,kind,channel,dedupe_key,status,title,body,image_path,
                 related_kind,related_id,next_attempt_at,expires_at,created_at,updated_at,quiet_allowed)
                values (?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?) on conflict(dedupe_key) do nothing""",
                (notification_id, kind, channel, f"{dedupe_key}:{channel}", title, body, image_path,
                 *(related if related is not None else (None, None)), next_attempt_at, expires_at, now, now, int(quiet_allowed)))
            revived = conn.execute("""update notification_outbox set status='pending',next_attempt_at=?,expires_at=?,
                updated_at=?,last_error_code=null where dedupe_key=? and status='skipped'""",
                (next_attempt_at, expires_at, now, f"{dedupe_key}:{channel}")).rowcount
            row = conn.execute("select notification_id from notification_outbox where dedupe_key=?",
                               (f"{dedupe_key}:{channel}",)).fetchone()
            notification_id = row["notification_id"]
            if revived:
                conn.execute("""update tasks set status='pending',next_run_at=?,error_reason=null,finished_at=null,
                    updated_at=? where task_id=? and status in ('success','skipped')""",
                    (next_attempt_at, now, f"notification_{notification_id}"))
            enqueue_task(conn, Task(task_id=f"notification_{notification_id}", task_type=TaskType.NOTIFICATION_DELIVER,
                target_kind="notification", target_id=notification_id, next_run_at=next_attempt_at), now=now)
        return notification_id

    def get(self, notification_id: str) -> dict | None:
        with self.connect_existing() as conn:
            row = conn.execute("select * from notification_outbox where notification_id=?", (notification_id,)).fetchone()
        return dict(row) if row is not None else None

    def claim(self, notification_id: str, *, now: str, lease_until: str) -> dict | None:
        token = uuid4().hex
        with self.connect() as conn:
            row = conn.execute("""update notification_outbox set status='sending',claim_token=?,lease_until=?,
                attempts=attempts+1,updated_at=? where notification_id=? and
                (status='pending' or (status='sending' and lease_until<=?)) and next_attempt_at<=?
                returning *""", (token, lease_until, now, notification_id, now, now)).fetchone()
        return dict(row) if row is not None else None

    def finish(self, row: dict, *, status: str, now: str, next_attempt_at: str,
               error_code: str | None = None, provider_message_id: str | None = None) -> bool:
        with self.connect() as conn:
            conn.execute("begin immediate")
            changed = conn.execute("""update notification_outbox set status=?, updated_at=?,next_attempt_at=?,
                last_error_code=?,provider_message_id=?,sent_at=?,claim_token=null,lease_until=null
                where notification_id=? and claim_token=?""", (status, now, next_attempt_at, error_code,
                    provider_message_id, now if status == 'sent' else None, row['notification_id'], row['claim_token'])).rowcount
            if changed and row['related_kind'] == 'auth_incident':
                conn.execute("""update auth_recovery_incidents set notification_status=case
                    when exists(select 1 from notification_outbox where related_kind='auth_incident'
                      and related_id=? and status='sent') then 'sent'
                    when exists(select 1 from notification_outbox where related_kind='auth_incident'
                      and related_id=? and status in ('pending','sending')) then 'pending'
                    when exists(select 1 from notification_outbox where related_kind='auth_incident'
                      and related_id=? and status in ('failed','expired')) then 'failed'
                    else 'skipped' end,
                    notified_at=case when ?='sent' then ? else notified_at end
                    where incident_id=?""", (row['related_id'], row['related_id'], row['related_id'], status, now, row['related_id']))
        return bool(changed)

    def latest_results(self) -> list[dict]:
        with self.connect_existing() as conn:
            rows = conn.execute("""select channel,status,last_error_code,sent_at,updated_at from (
                select channel,status,last_error_code,sent_at,updated_at,
                row_number() over(partition by channel order by updated_at desc,notification_id desc) as position
                from notification_outbox) where position=1 order by channel""").fetchall()
        return [dict(row) for row in rows]

    def image_in_use(self, path: str) -> bool:
        with self.connect_existing() as conn:
            return conn.execute("""select 1 from notification_outbox where image_path=?
                and status in ('pending','sending') limit 1""", (path,)).fetchone() is not None

    def cancel_incident(self, incident_id: str, *, now: str, include_text: bool = True) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("""update notification_outbox set status='expired',updated_at=?,claim_token=null,
                lease_until=null where related_kind='auth_incident' and related_id=?
                and (kind='auth_recovery_qr' or (? and kind='auth_recovery_text')) and status in ('pending','sending')
                returning image_path""", (now, incident_id, int(include_text))).fetchall()
        return [row['image_path'] for row in rows if row['image_path'] is not None]

    def incident_has_delivery(self, incident_id: str) -> bool:
        with self.connect_existing() as conn:
            return conn.execute("""select 1 from notification_outbox where related_kind='auth_incident'
                and related_id=? and kind in ('auth_recovery_qr','auth_recovery_text')
                and status in ('pending','sending','sent','failed','expired') limit 1""", (incident_id,)).fetchone() is not None

    def by_dedupe(self, dedupe_key: str, *, channel: str) -> dict | None:
        with self.connect_existing() as conn:
            row = conn.execute("select * from notification_outbox where dedupe_key=?", (f"{dedupe_key}:{channel}",)).fetchone()
        return dict(row) if row is not None else None
