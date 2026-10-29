"""Persist QR round transitions together with their next durable action."""
from backend.domain.tasks import Task, TaskType
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.tasks.finalization import enqueue_task


class AuthQrRepository(InfoOSRepository):
    def get(self, incident_id: str) -> dict | None:
        with self.connect_existing() as conn:
            row = conn.execute('select * from auth_qr_rounds where incident_id=?', (incident_id,)).fetchone()
        return dict(row) if row is not None else None

    def start(self, incident: dict, *, now: str, day: str, day_limit: int, manual: bool) -> dict:
        incident_id = incident['incident_id']
        with self.connect() as conn:
            conn.execute('begin immediate')
            conn.execute('''insert into auth_qr_rounds(incident_id,next_round_at,day,tab_site,updated_at)
                values(?,?,?,?,?) on conflict(incident_id) do nothing''',
                (incident_id, now, day, f'authqr-{incident_id}', now))
            row = dict(conn.execute('select * from auth_qr_rounds where incident_id=?', (incident_id,)).fetchone())
            actionable = conn.execute("select 1 from auth_recovery_incidents where incident_id=? and status='awaiting_user'", (incident_id,)).fetchone()
            if actionable is None or row['status'] != 'idle':
                return row
            today = row['rounds_today'] if row['day'] == day else 0
            if not manual and (row['next_round_at'] > now or today >= day_limit):
                return row
            conn.execute('''update auth_qr_rounds set status='capturing',round_no=round_no+1,codes_sent=0,
                code_expires_at=null,day=?,rounds_today=?,manual=?,updated_at=? where incident_id=?''',
                (day, today + 1, int(manual), now, incident_id))
            row = dict(conn.execute('select * from auth_qr_rounds where incident_id=?', (incident_id,)).fetchone())
            self._capture(conn, row, platform=incident['platform'], now=now)
            enqueue_task(conn, Task(task_id=f"qrcheck_{incident_id}_{row['round_no']}", task_type=TaskType.AUTH_QR_CHECK,
                target_kind='auth_incident', target_id=incident_id), now=now)
        return row

    @staticmethod
    def _capture(conn, row: dict, *, platform: str, now: str, verify: bool = False) -> None:
        code = row['codes_sent'] + 1
        suffix = 'verify' if verify else str(code)
        enqueue_task(conn, Task(task_id=f"qrcapture_{row['incident_id']}_{row['round_no']}_{suffix}",
            task_type=TaskType.AUTH_QR_CAPTURE, target_kind='auth_incident', target_id=row['incident_id'],
            artifacts_summary={'queue_platform': platform, 'round_no': row['round_no'], 'code': code, 'verify': verify}), now=now)

    def captured(self, row: dict, *, expires_at: str, lease_id: str | None, now: str) -> bool:
        with self.connect() as conn:
            return bool(conn.execute('''update auth_qr_rounds set status='waiting',codes_sent=codes_sent+1,
                code_expires_at=?,tab_lease_id=?,updated_at=? where incident_id=? and round_no=?
                and codes_sent=? and status='capturing' ''', (expires_at, lease_id, now, row['incident_id'],
                    row['round_no'], row['codes_sent'])).rowcount)

    def next_capture(self, row: dict, *, platform: str, now: str, verify: bool = False, manual: bool = False) -> None:
        with self.connect() as conn:
            conn.execute('begin immediate')
            changed = conn.execute("""update auth_qr_rounds set status='capturing',updated_at=?,
                code_expires_at=null,manual=case when ? then 1 else manual end
                where incident_id=? and round_no=? and status='waiting' and codes_sent=?""",
                (now, int(manual), row['incident_id'], row['round_no'], row['codes_sent'])).rowcount
            if changed:
                self._capture(conn, row, platform=platform, now=now, verify=verify)

    def restart_after_close(self, incident_id: str, *, request_id: str, now: str) -> None:
        with self.connect() as conn:
            enqueue_task(conn, Task(task_id=f'qrmanual_{request_id}', task_type=TaskType.AUTH_QR_CHECK,
                target_kind='auth_incident', target_id=incident_id, next_run_at=now,
                artifacts_summary={'manual_restart': True}), now=now)

    def latest_awaiting(self, platform: str) -> dict | None:
        with self.connect_existing() as conn:
            row = conn.execute("""select * from auth_recovery_incidents
                where platform=? and status='awaiting_user'
                order by created_at desc,incident_id desc limit 1""", (platform,)).fetchone()
        return dict(row) if row is not None else None

    def close(self, incident_id: str, *, now: str, next_round_at: str) -> None:
        with self.connect() as conn:
            conn.execute("""update auth_qr_rounds set status='closing',next_round_at=?,updated_at=?
                where incident_id=? and status not in ('resolved','closing')""", (next_round_at, now, incident_id))

    def closed(self, incident_id: str, *, now: str, resolved: bool) -> None:
        with self.connect() as conn:
            conn.execute("""update auth_qr_rounds set status=?,tab_lease_id=null,code_expires_at=null,updated_at=?
                where incident_id=?""", ('resolved' if resolved else 'idle', now, incident_id))

    def actionable_ids(self) -> list[str]:
        with self.connect_existing() as conn:
            rows = conn.execute("""select incident_id from auth_recovery_incidents where status='awaiting_user'
                union select incident_id from auth_qr_rounds where status not in ('idle','resolved')""").fetchall()
        return [r['incident_id'] for r in rows]

    def has_open_tabs(self) -> bool:
        with self.connect_existing() as conn:
            return conn.execute("select 1 from auth_qr_rounds where tab_site != '' and status in ('capturing','waiting','closing') limit 1").fetchone() is not None
