"""Login incidents for external services with no InfoOS fetch circuit."""
from uuid import uuid4

from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository


class ExternalAuthRecoveryRepository(AuthRecoveryRepository):
    def create_or_reuse(self, *, platform: str, occurred_at: str) -> dict:
        with self.connect() as conn:
            conn.execute('begin immediate')
            row = conn.execute("""select incident_id from auth_recovery_incidents
                where platform=? and status in ('awaiting_user','verifying')
                order by outage_epoch desc limit 1""", (platform,)).fetchone()
            if row is not None:
                incident_id = row['incident_id']
            else:
                epoch = conn.execute('select coalesce(max(outage_epoch),0)+1 from auth_recovery_incidents where platform=?',
                                     (platform,)).fetchone()[0]
                incident_id = f'authrec_{uuid4().hex}'
                conn.execute("""insert into auth_recovery_incidents
                    (incident_id,platform,outage_epoch,reason_code,status,created_at,updated_at,notification_status)
                    values(?,?,?,'platform_not_logged_in','awaiting_user',?,?,'pending')""",
                    (incident_id, platform, epoch, occurred_at, occurred_at))
        return self.get(incident_id)
