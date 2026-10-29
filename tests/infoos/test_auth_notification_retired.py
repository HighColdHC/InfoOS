"""Unknown platforms finish notification claims without scheduling delivery."""
import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.workflows import auth_recovery
from backend.domain.tasks import Task, TaskType
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository

NOW = '2026-10-22T01:00:00Z'


@pytest.mark.parametrize('platform', ['scys', 'unregistered-platform'])
def test_unregistered_platform_skips_notification_and_releases_claim(tmp_path, monkeypatch, platform):
    tasks = TaskRepository.for_base_dir(tmp_path)
    tasks.enqueue(Task('fetch-retired', TaskType.FETCH, 'source', 'synthetic'))
    repo = AuthRecoveryRepository(tmp_path)
    incident, _ = repo.create_or_link(platform=platform, outage_epoch=1,
        reason_code='platform_not_logged_in', task_id='fetch-retired', occurred_at=NOW)
    sends = []
    monkeypatch.setattr(auth_recovery, 'enqueue_notification', lambda *a, **kw: sends.append(kw) or ['sent'])
    assert auth_recovery.attempt_auth_notification(tmp_path, incident['incident_id'], attempted_at=NOW) == 'skipped'
    row = repo.get(incident['incident_id'])
    assert row['notification_status'] == 'skipped'
    assert row['notification_attempts'] == 1
    assert row['notification_claim_token'] is None
    assert row['notification_lease_until'] is None
    assert sends == []
    with repo.connect_existing() as conn:
        assert conn.execute('select count(*) from notification_outbox').fetchone()[0] == 0
        assert conn.execute('select count(*) from auth_qr_rounds').fetchone()[0] == 0
        assert conn.execute('select count(*) from tasks').fetchone()[0] == 1
