from __future__ import annotations

import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.acquisition_failures import (
    AcquisitionFailure,
    AcquisitionFailureError,
)
from backend.application.workflows.auth_recovery import (
    complete_committed_auth_failure,
    drain_auth_recovery_notifications,
    record_auth_incident,
    resolve_verified_auth_recovery,
)
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.infrastructure.notifications.channels.email import EmailChannel
from backend.infrastructure.browser.console_access import COOKIE_NAME
from backend.infrastructure.runtime.environment import public_base_url
from backend.infrastructure.runtime.setup_state import (
    AccountState,
    AccountStatus,
    load_state,
    update_account_state,
)
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


NOW = "2026-07-26T01:02:03Z"
SAME_ORIGIN_HEADERS = {
    "origin": "http://testserver",
    "sec-fetch-site": "same-origin",
}


def _client(base_dir) -> TestClient:
    return TestClient(create_app(base_dir), headers=SAME_ORIGIN_HEADERS)


def _task(base_dir, task_id: str, *, status: TaskStatus = TaskStatus.PENDING) -> None:
    repo = TaskRepository.for_base_dir(base_dir)
    repo.enqueue(
        Task(
            task_id,
            TaskType.FETCH,
            "source",
            f"source-{task_id}",
            status=status,
        )
    )


def _incident(base_dir, task_id: str = "fetch-one") -> dict:
    initialize_runtime_storage(base_dir)
    _task(base_dir, task_id)
    circuit = RuntimeCircuitRepository(base_dir).open_manual(
        "platform",
        "x",
        occurred_at=NOW,
        reason_code=FetchFailureReason.PLATFORM_NOT_LOGGED_IN.value,
        user_action="login_or_config_required",
        task_id=task_id,
    )
    incident, _ = AuthRecoveryRepository(base_dir).create_or_link(
        platform="x",
        outage_epoch=circuit["outage_epoch"],
        reason_code=FetchFailureReason.PLATFORM_NOT_LOGGED_IN.value,
        task_id=task_id,
        occurred_at=NOW,
    )
    return incident


def _block_task(base_dir, task_id: str) -> None:
    db_path = initialize_runtime_storage(base_dir)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            update tasks set status = 'blocked', attempt = 1, max_attempts = 1,
              next_run_at = null, error_reason = 'Login required',
              claimed_by = 'old-worker', lease_expires_at = '2026-07-26T02:00:00Z',
              heartbeat_at = ?, started_at = ?, finished_at = ?,
              result_version = 'old', completion_token = 'old-token',
              last_failure_class = 'platform_not_logged_in'
            where task_id = ?
            """,
            (NOW, NOW, NOW, task_id),
        )


def test_completed_schema_rejects_missing_tables(tmp_path) -> None:
    from backend.infrastructure.storage.migrations.v0001_baseline import SchemaDriftError
    db_path = initialize_runtime_storage(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("drop table auth_recovery_incident_tasks")
        conn.execute("drop table auth_recovery_incidents")
    with pytest.raises(SchemaDriftError, match="missing table"):
        initialize_runtime_storage(tmp_path)


def test_manual_outage_epoch_dedupes_incident_and_links_tasks(
    tmp_path, monkeypatch
) -> None:
    initialize_runtime_storage(tmp_path)
    _task(tmp_path, "fetch-one")
    _task(tmp_path, "fetch-two")
    circuit_one = RuntimeCircuitRepository(tmp_path).open_manual(
        "platform",
        "x",
        occurred_at=NOW,
        reason_code="platform_not_logged_in",
        user_action="login_or_config_required",
    )
    circuit_two = RuntimeCircuitRepository(tmp_path).open_manual(
        "platform",
        "x",
        occurred_at="2026-07-26T01:03:00Z",
        reason_code="platform_not_logged_in",
        user_action="login_or_config_required",
    )
    assert circuit_one["outage_epoch"] == 1
    assert circuit_two["outage_epoch"] == 1
    sends: list[str] = []
    _capture_outbox(tmp_path, monkeypatch, sends)
    first = record_auth_incident(
        tmp_path, circuit=circuit_one, task_id="fetch-one", occurred_at=NOW
    )
    second = record_auth_incident(
        tmp_path,
        circuit=circuit_two,
        task_id="fetch-two",
        occurred_at="2026-07-26T01:03:00Z",
    )
    assert first["incident_id"] == second["incident_id"]
    assert sends == [first["incident_id"]]
    assert AuthRecoveryRepository(tmp_path).get(first["incident_id"])[
        "affected_task_count"
    ] == 2


def test_failed_notification_retries_and_sent_notification_dedupes(tmp_path, monkeypatch) -> None:
    from backend.application.tasks.handlers.notification_deliver import deliver_notification
    from backend.infrastructure.notifications.channels import ChannelResult
    from backend.infrastructure.notifications.channels.email import EmailChannel
    from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository
    _task(tmp_path, 'fetch-one')
    sends = []
    _capture_outbox(tmp_path, monkeypatch, sends)
    circuit = RuntimeCircuitRepository(tmp_path).open_manual('platform', 'x', occurred_at=NOW,
        reason_code='platform_not_logged_in', user_action='login_or_config_required')
    incident = record_auth_incident(tmp_path, circuit=circuit, task_id='fetch-one', occurred_at=NOW)
    repo = NotificationOutboxRepository(tmp_path)
    with repo.connect_existing() as conn:
        notification_id = conn.execute('select notification_id from notification_outbox').fetchone()[0]
    outcomes = iter((ChannelResult('retryable', 'transport_error'), ChannelResult('sent')))
    monkeypatch.setattr(EmailChannel, 'send', lambda *a, **kw: next(outcomes))
    assert deliver_notification(tmp_path, notification_id, now=NOW)['status'] == 'pending'
    assert AuthRecoveryRepository(tmp_path).get(incident['incident_id'])['notification_status'] == 'pending'
    deliver_notification(tmp_path, notification_id, now='2026-07-26T01:02:10Z')
    assert AuthRecoveryRepository(tmp_path).get(incident['incident_id'])['notification_status'] == 'sent'
    record_auth_incident(tmp_path, circuit=circuit, task_id='fetch-one', occurred_at='2026-07-26T01:02:20Z')
    assert len(sends) == 1


def test_expired_notification_claim_recovers_after_process_exit(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    repo = AuthRecoveryRepository(tmp_path)
    abandoned = repo.claim_notification(
        incident["incident_id"],
        lease_until="2026-07-26T01:03:03Z",
        updated_at=NOW,
    )
    assert abandoned is not None
    sends: list[str] = []
    _capture_outbox(tmp_path, monkeypatch, sends)
    circuit = RuntimeCircuitRepository(tmp_path).get("platform", "x")
    record_auth_incident(
        tmp_path,
        circuit=circuit,
        task_id="fetch-one",
        occurred_at="2026-07-26T01:03:02Z",
    )
    assert sends == []
    recovered = record_auth_incident(
        tmp_path,
        circuit=circuit,
        task_id="fetch-one",
        occurred_at="2026-07-26T01:03:04Z",
    )
    assert sends == [incident["incident_id"]]
    assert recovered["notification_status"] == "pending"


def test_reconcile_worker_drains_expired_notifications_with_attempt_cap(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    repo = AuthRecoveryRepository(tmp_path)
    repo.claim_notification(
        incident["incident_id"],
        lease_until="2026-07-26T01:03:03Z",
        updated_at=NOW,
    )
    _task(tmp_path, "fetch-capped")
    capped, _ = repo.create_or_link(
        platform="youtube",
        outage_epoch=1,
        reason_code="platform_not_logged_in",
        task_id="fetch-capped",
        occurred_at=NOW,
    )
    with sqlite3.connect(initialize_runtime_storage(tmp_path)) as conn:
        conn.execute(
            """
            update auth_recovery_incidents
            set notification_status = 'failed', notification_attempts = 5,
              notification_lease_until = null
            where incident_id = ?
            """,
            (capped["incident_id"],),
        )
    sends: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.utc_now_seconds_z",
        lambda: "2026-07-26T01:03:04Z",
    )
    _capture_outbox(tmp_path, monkeypatch, sends)
    monkeypatch.setattr(
        "backend.application.workflows.pipeline_reconciliation.reconcile_pipeline",
        lambda base_dir: {"scanned": 0},
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(
        Task(
            "reconcile-auth-notifications",
            TaskType.PIPELINE_RECONCILE,
            "global",
            "pipeline",
        )
    )
    result = run_due_tasks(
        tmp_path,
        now="2026-07-26T01:03:04Z",
        task_types=[TaskType.PIPELINE_RECONCILE],
    )
    assert sends == [incident["incident_id"]]
    assert result["succeeded"] == 1
    reconciled = TaskRepository.for_base_dir(tmp_path).get(
        "reconcile-auth-notifications"
    )
    assert reconciled.artifacts_summary["auth_recovery_notifications"] == {
        "claimed": 1,
        "pending": 1,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert AuthRecoveryRepository(tmp_path).get(capped["incident_id"])[
        "notification_attempts"
    ] == 5


def test_reconcile_backfills_legacy_manual_login_circuit_and_parks_tasks(
    tmp_path, monkeypatch
) -> None:
    initialize_runtime_storage(tmp_path)
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "reddit-one",
            "platform": "reddit",
            "source_type": "subreddit",
            "identifier": "python",
            "display_name": "Python",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "raw_metadata": {},
        }
    )
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "reddit-disabled",
            "platform": "reddit",
            "source_type": "subreddit",
            "identifier": "disabled",
            "display_name": "Disabled",
            "cadence_hours": 24,
            "enabled": False,
            "connector_status": "supported",
            "raw_metadata": {},
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("legacy-one", TaskType.FETCH, "source", "reddit-one"))
    repo.enqueue(Task("legacy-two", TaskType.FETCH, "source", "reddit-one"))
    repo.enqueue(
        Task("legacy-disabled", TaskType.FETCH, "source", "reddit-disabled")
    )
    circuit = RuntimeCircuitRepository(tmp_path).open_manual(
        "platform",
        "reddit",
        occurred_at=NOW,
        reason_code="platform_not_logged_in",
        user_action="login_or_config_required",
    )
    sends: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: tmp_path / "setup-state.json",
    )
    _capture_outbox(tmp_path, monkeypatch, sends)

    counts = drain_auth_recovery_notifications(
        tmp_path,
        now="2026-07-26T01:03:04Z",
    )

    assert counts == {"claimed": 1, "sent": 0, "pending": 1, "failed": 0, "skipped": 0}
    assert len(sends) == 1
    incidents = AuthRecoveryRepository(tmp_path).list_actionable()
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["platform"] == "reddit"
    assert incident["outage_epoch"] == circuit["outage_epoch"]
    assert incident["affected_task_count"] == 2
    for task_id in ("legacy-one", "legacy-two"):
        task = repo.get(task_id)
        assert task is not None
        assert task.status is TaskStatus.BLOCKED
        assert task.next_run_at is None
        assert task.last_failure_class == "platform_not_logged_in"
    assert repo.get("legacy-disabled").status is TaskStatus.PENDING
    event = TaskEventRepository(tmp_path).list_events(
        task_id="legacy-one",
        event_type="auth_recovery_required",
    )
    assert len(event) == 1
    assert event[0]["payload"]["incident_id"] == incident["incident_id"]

    assert drain_auth_recovery_notifications(
        tmp_path,
        now="2026-07-26T01:04:04Z",
    ) == {"claimed": 0, "sent": 0, "pending": 0, "failed": 0, "skipped": 0}
    assert sends == [incident["incident_id"]]


def test_post_commit_setup_and_notification_failures_are_isolated(
    tmp_path, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.mark_account_needs_login",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("setup failed")),
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.attempt_auth_notification",
        lambda *args, **kwargs: calls.append("notification") or "sent",
    )
    complete_committed_auth_failure(
        tmp_path,
        incident_id="authrec_safe",
        platform="x",
        occurred_at=NOW,
    )
    assert calls == ["notification"]

    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.mark_account_needs_login",
        lambda *args, **kwargs: calls.append("setup"),
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.attempt_auth_notification",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("smtp failed")),
    )
    complete_committed_auth_failure(
        tmp_path,
        incident_id="authrec_safe",
        platform="x",
        occurred_at=NOW,
    )
    assert calls == ["notification", "setup"]


def test_public_base_url_requires_strict_https_origin_and_email_adapter_is_text_only(
    monkeypatch,
) -> None:
    for invalid in (
        "http://infoos.example",
        "https://user@infoos.example",
        "https://infoos.example/",
        "https://infoos.example/path",
        "https://infoos.example?q=1",
        "https://infoos.example#fragment",
    ):
        monkeypatch.setenv("INFOOS_PUBLIC_BASE_URL", invalid)
        assert public_base_url() is None
    monkeypatch.setenv("INFOOS_PUBLIC_BASE_URL", "https://infoos.example:8443")
    assert public_base_url() == "https://infoos.example:8443"

    config = {
        "enabled": True,
        "email": {
            "smtp_host": "smtp.example",
            "smtp_port": 587,
            "username": "sender",
            "password": "secret",
            "from_email": "sender@example.com",
            "to_email": "owner@example.com",
        },
    }
    captured: dict = {}
    monkeypatch.setattr(
        "backend.infrastructure.notifications.channels.email.send_email",
        lambda **kwargs: captured.update(kwargs) or True,
    )
    result = EmailChannel(config).send(title="X 需要重新登录", body="请在局域网 Web 环境页恢复登录。",
                                      image_path="unused-qr.png", message_id="synthetic")
    assert result.status == "sent"
    assert captured["body"] == "请在局域网 Web 环境页恢复登录。"
    assert "image_path" not in captured


def test_api_get_open_console_and_resolved_conflict(tmp_path, monkeypatch) -> None:
    incident = _incident(tmp_path)
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.launch_platform_login",
        lambda platform: {
            "ok": True,
            "platform": platform,
            "console_url": "/browser-console/",
        },
    )
    client = _client(tmp_path)
    response = client.get(f"/api/auth-recoveries/{incident['incident_id']}")
    assert response.status_code == 200
    assert response.json()["incident"]["platform_label"] == "X"
    opened = client.post(
        f"/api/auth-recoveries/{incident['incident_id']}/open-console"
    )
    assert opened.status_code == 202
    assert f"{COOKIE_NAME}=" in opened.headers["set-cookie"]
    assert "HttpOnly" in opened.headers["set-cookie"]
    assert "Secure" in opened.headers["set-cookie"]
    assert "Path=/browser-console/" in opened.headers["set-cookie"]

    with sqlite3.connect(initialize_runtime_storage(tmp_path)) as conn:
        conn.execute(
            "update auth_recovery_incidents set status = 'resolved' where incident_id = ?",
            (incident["incident_id"],),
        )
    conflict = client.post(
        f"/api/auth-recoveries/{incident['incident_id']}/open-console"
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "recovery_not_actionable"
    missing = client.get("/api/auth-recoveries/missing")
    assert missing.status_code == 404
    assert set(missing.json()) == {"code", "message"}


def test_auth_recovery_mutations_reject_missing_and_cross_origin(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    launches: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.launch_platform_login",
        lambda platform: launches.append(platform)
        or {"ok": True, "console_url": "/browser-console/"},
    )
    client = TestClient(create_app(tmp_path))
    path = f"/api/auth-recoveries/{incident['incident_id']}/open-console"
    assert client.post(
        path,
        headers={"origin": "", "sec-fetch-site": "", "referer": ""},
    ).status_code == 403
    assert client.post(
        path,
        headers={
            "origin": "https://attacker.example",
            "sec-fetch-site": "cross-site",
        },
    ).status_code == 403
    verify_path = f"/api/auth-recoveries/{incident['incident_id']}/verify"
    assert client.post(
        verify_path,
        headers={
            "origin": "https://attacker.example",
            "sec-fetch-site": "cross-site",
        },
    ).status_code == 403
    assert launches == []


def test_open_console_maps_unsupported_and_runtime_failures(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    client = _client(tmp_path)
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.launch_platform_login",
        lambda platform: (_ for _ in ()).throw(ValueError("unsupported")),
    )
    unsupported = client.post(
        f"/api/auth-recoveries/{incident['incident_id']}/open-console"
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["code"] == "unsupported_login_platform"

    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.launch_platform_login",
        lambda platform: {"ok": False, "error": "sensitive runtime detail"},
    )
    unavailable = client.post(
        f"/api/auth-recoveries/{incident['incident_id']}/open-console"
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "code": "browser_runtime_unavailable",
        "message": "Browser runtime is unavailable",
    }


def test_failed_probe_changes_only_incident(tmp_path, monkeypatch) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    before = RuntimeCircuitRepository(tmp_path).get("platform", "x")
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(
            status=AccountStatus.NEEDS_LOGIN,
            last_checked="2026-07-26T01:04:00Z",
            error="sensitive upstream detail",
        ),
    )
    response = _client(tmp_path).post(
        f"/api/auth-recoveries/{incident['incident_id']}/verify"
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["verification"]["code"] == "platform_session_not_ready"
    assert response.json()["incident"]["status"] == "awaiting_user"
    assert RuntimeCircuitRepository(tmp_path).get("platform", "x") == before
    task = TaskRepository.for_base_dir(tmp_path).get("fetch-one")
    assert task.status is TaskStatus.BLOCKED
    assert task.attempt == 1


def test_manual_incident_is_invisible_until_terminal_commit_then_recovers(
    tmp_path, monkeypatch
) -> None:
    initialize_runtime_storage(tmp_path)
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "source-fetch-race",
            "platform": "x",
            "source_type": "creator",
            "identifier": "fetch-race",
            "display_name": "fetch-race",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
        }
    )
    _task(tmp_path, "fetch-race")
    db_path = initialize_runtime_storage(tmp_path)
    setup_path = tmp_path / "setup-state.json"
    update_account_state(
        setup_path,
        "x",
        lambda previous: AccountState(
            status=AccountStatus.VERIFIED,
            persisted=True,
            persistence_checked_at=NOW,
            persistence_stage="restart_verified",
            persistence_error="old evidence",
        ),
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: setup_path,
    )
    notifications: list[str] = []
    _capture_outbox(tmp_path, monkeypatch, notifications)
    from backend.application.tasks import processor_execution

    monkeypatch.setattr(
        "backend.application.tasks.auth_recheck.probe_platform",
        lambda platform: AccountState(status=AccountStatus.NEEDS_LOGIN, error="Not logged in"),
    )
    original_transaction_effect = (
        processor_execution.platform_circuits.record_manual_platform_failure_to_connection
    )
    incident_ready = threading.Event()
    allow_terminal_commit = threading.Event()
    worker_errors: list[BaseException] = []

    def pause_before_commit(*args, **kwargs):
        effect = original_transaction_effect(*args, **kwargs)
        incident_ready.set()
        if not allow_terminal_commit.wait(timeout=3):
            raise TimeoutError("test did not release terminal commit")
        return effect

    monkeypatch.setattr(
        processor_execution.platform_circuits,
        "record_manual_platform_failure_to_connection",
        pause_before_commit,
    )
    failure = AcquisitionFailure(
        reason=FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
        message="sensitive login failure",
        retryable=False,
        user_action="login",
        diagnostic={},
    )

    def run_worker() -> None:
        try:
            run_due_tasks(
                tmp_path,
                now=NOW,
                handlers={
                    TaskType.FETCH: lambda *_: (_ for _ in ()).throw(
                        AcquisitionFailureError(failure)
                    )
                },
            )
        except BaseException as exc:
            worker_errors.append(exc)

    worker = threading.Thread(target=run_worker)
    worker.start()
    assert incident_ready.wait(timeout=3)
    account = load_state(setup_path).accounts["x"]
    assert account.status is AccountStatus.VERIFIED
    assert account.persisted is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "select count(*) from auth_recovery_incidents"
        ).fetchone()[0] == 0
        assert conn.execute(
            "select count(*) from runtime_circuits"
        ).fetchone()[0] == 0
    assert notifications == []

    allow_terminal_commit.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert worker_errors == []
    assert TaskRepository.for_base_dir(tmp_path).get("fetch-race").status is TaskStatus.BLOCKED
    with sqlite3.connect(db_path) as conn:
        incident_id = conn.execute(
            "select incident_id from auth_recovery_incidents"
        ).fetchone()[0]
    account = load_state(setup_path).accounts["x"]
    assert account.status is AccountStatus.NEEDS_LOGIN
    assert account.persisted is False
    assert notifications == [incident_id]
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(status=AccountStatus.VERIFIED),
    )
    client = _client(tmp_path)
    recovered = client.post(f"/api/auth-recoveries/{incident_id}/verify")
    assert recovered.status_code == 200
    assert recovered.json()["ok"] is True
    assert TaskRepository.for_base_dir(tmp_path).get("fetch-race").status is TaskStatus.PENDING
    recovered_account = load_state(setup_path).accounts["x"]
    assert recovered_account.status is AccountStatus.VERIFIED
    assert recovered_account.persisted is False
    assert recovered_account.persistence_checked_at is None
    assert recovered_account.persistence_stage is None
    assert recovered_account.persistence_error is None


def test_no_email_incident_is_discoverable_and_recovers_from_environment(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    db_path = initialize_runtime_storage(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            update auth_recovery_incidents
            set notification_status = 'skipped'
            where incident_id = ?
            """,
            (incident["incident_id"],),
        )
    setup_path = tmp_path / "setup-state.json"
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: setup_path,
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(status=AccountStatus.VERIFIED),
    )
    client = _client(tmp_path)
    snapshot = client.get("/api/collection-environment").json()
    pending = snapshot["pending_auth_recoveries"]
    assert pending == [
        {
            "incident_id": incident["incident_id"],
            "platform": "x",
            "status": "awaiting_user",
            "reason_code": "platform_not_logged_in",
            "affected_task_count": 1,
            "created_at": NOW,
            "updated_at": NOW,
            "last_error_code": None,
            "open_console_url": (
                f"/api/auth-recoveries/{incident['incident_id']}/open-console"
            ),
            "verify_url": f"/api/auth-recoveries/{incident['incident_id']}/verify",
        }
    ]
    recovered = client.post(pending[0]["verify_url"])
    assert recovered.status_code == 200
    assert recovered.json()["ok"] is True
    assert TaskRepository.for_base_dir(tmp_path).get("fetch-one").status is TaskStatus.PENDING
    assert client.get("/api/collection-environment").json()[
        "pending_auth_recoveries"
    ] == []


def test_notification_drain_retries_skipped_incident_after_configuration_becomes_ready(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    repo = AuthRecoveryRepository(tmp_path)
    with repo.connect() as conn:
        conn.execute(
            """
            update auth_recovery_incidents
            set notification_status = 'skipped'
            where incident_id = ?
            """,
            (incident["incident_id"],),
        )
    sent: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.readiness",
        lambda config: {"email": True, "feishu": False},
        raising=False,
    )
    _capture_outbox(tmp_path, monkeypatch, sent)

    counts = drain_auth_recovery_notifications(
        tmp_path,
        now="2026-07-26T01:04:04Z",
    )

    assert counts == {"claimed": 1, "sent": 0, "pending": 1, "failed": 0, "skipped": 0}
    assert sent == [incident["incident_id"]]
    assert repo.get(incident["incident_id"])["notification_status"] == "pending"


def test_resolution_cancels_delayed_notification_claim(tmp_path, monkeypatch) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    repo = AuthRecoveryRepository(tmp_path)
    claim_token = repo.claim_notification(
        incident["incident_id"],
        lease_until="2026-07-26T01:10:00Z",
        updated_at=NOW,
    )
    assert claim_token is not None
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(status=AccountStatus.VERIFIED),
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: tmp_path / "setup-state.json",
    )
    response = _client(tmp_path).post(
        f"/api/auth-recoveries/{incident['incident_id']}/verify"
    )
    assert response.status_code == 200
    stored = repo.get(incident["incident_id"])
    assert stored["status"] == "resolved"
    assert stored["notification_status"] == "skipped"
    assert stored["notification_claim_token"] is None
    assert stored["notification_lease_until"] is None
    assert repo.complete_notification(
        incident["incident_id"],
        claim_token=claim_token,
        status="sent",
        notified_at="2026-07-26T01:11:00Z",
        updated_at="2026-07-26T01:11:00Z",
    ) is False
    assert repo.list_notification_candidates(
        now="2026-07-26T01:11:00Z"
    ) == []


def test_passed_probe_recovers_only_linked_matching_tasks(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    for task_id in ("linked-good", "linked-other-failure", "unlinked-good"):
        _task(tmp_path, task_id)
    repo = AuthRecoveryRepository(tmp_path)
    repo.create_or_link(
        platform="x",
        outage_epoch=1,
        reason_code="platform_not_logged_in",
        task_id="linked-good",
        occurred_at=NOW,
    )
    for task_id in ("fetch-one", "linked-good", "linked-other-failure", "unlinked-good"):
        _block_task(tmp_path, task_id)
    db_path = initialize_runtime_storage(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "update tasks set last_failure_class = 'network_timeout' where task_id = 'linked-other-failure'"
        )
    setup_path = tmp_path / "setup-state.json"
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: setup_path,
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(
            status=AccountStatus.VERIFIED,
            last_checked="2026-07-26T01:05:00Z",
        ),
    )
    response = _client(tmp_path).post(
        f"/api/auth-recoveries/{incident['incident_id']}/verify"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["incident"]["recovered_task_count"] == 2
    assert RuntimeCircuitRepository(tmp_path).get("platform", "x")["state"] == "closed"
    task_repo = TaskRepository.for_base_dir(tmp_path)
    for task_id in ("fetch-one", "linked-good"):
        task = task_repo.get(task_id)
        assert task.status is TaskStatus.PENDING
        assert task.attempt == 2
        assert task.max_attempts == 2
        assert task.next_run_at is not None
        assert task.claimed_by is None
        assert task.finished_at is None
        assert task.completion_token is None
    assert task_repo.get("linked-other-failure").status is TaskStatus.BLOCKED
    assert task_repo.get("unlinked-good").status is TaskStatus.BLOCKED
    assert load_state(setup_path).accounts["x"].status is AccountStatus.VERIFIED

    with sqlite3.connect(db_path) as conn:
        event_rows = [
            (row[0], json.loads(row[1]))
            for row in conn.execute(
                """
                select event_type, payload from task_events
                where event_type in ('auth_recovery_scheduled', 'circuit_closed')
                """
            )
        ]
    events = [payload for event_type, payload in event_rows if event_type == "auth_recovery_scheduled"]
    assert len(events) == 2
    assert all(set(event) == {"incident_id", "outage_epoch", "platform", "version"} for event in events)
    close_events = [payload for event_type, payload in event_rows if event_type == "circuit_closed"]
    assert close_events == [
        {
            "incident_id": incident["incident_id"],
            "outage_epoch": 1,
            "platform": "x",
            "scope_key": "x",
            "scope_kind": "platform",
            "version": 1,
        }
    ]


def test_probe_exception_returns_503_and_can_retry(tmp_path, monkeypatch) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    setup_path = tmp_path / "setup-state.json"
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: setup_path,
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: (_ for _ in ()).throw(RuntimeError("secret probe error")),
    )
    client = _client(tmp_path)
    unavailable = client.post(
        f"/api/auth-recoveries/{incident['incident_id']}/verify"
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "code": "recovery_verification_unavailable",
        "message": "Auth recovery verification is temporarily unavailable",
    }
    after_failure = AuthRecoveryRepository(tmp_path).get(incident["incident_id"])
    assert after_failure["status"] == "awaiting_user"
    assert after_failure["last_error_code"] == "recovery_verification_unavailable"

    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(status=AccountStatus.VERIFIED),
    )
    retried = client.post(
        f"/api/auth-recoveries/{incident['incident_id']}/verify"
    )
    assert retried.status_code == 200
    assert retried.json()["ok"] is True


def test_resolve_exception_resets_verification_for_retry(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(status=AccountStatus.VERIFIED),
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: tmp_path / "setup-state.json",
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.resolve_verified_auth_recovery",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("sensitive sqlite failure")
        ),
    )
    response = _client(tmp_path).post(
        f"/api/auth-recoveries/{incident['incident_id']}/verify"
    )
    assert response.status_code == 503
    assert response.json()["code"] == "recovery_verification_unavailable"
    stored = AuthRecoveryRepository(tmp_path).get(incident["incident_id"])
    assert stored["status"] == "awaiting_user"
    assert stored["verification_started_at"] is None


def test_setup_projection_failure_does_not_undo_resolved_recovery(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(status=AccountStatus.VERIFIED),
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery._persist_verified_account",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("sensitive setup failure")
        ),
    )

    response = _client(tmp_path).post(
        f"/api/auth-recoveries/{incident['incident_id']}/verify"
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert AuthRecoveryRepository(tmp_path).get(incident["incident_id"])["status"] == "resolved"
    assert RuntimeCircuitRepository(tmp_path).get("platform", "x")["state"] == "closed"
    assert TaskRepository.for_base_dir(tmp_path).get("fetch-one").status is TaskStatus.PENDING


def test_stale_verification_lease_can_be_reclaimed(tmp_path, monkeypatch) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    with sqlite3.connect(initialize_runtime_storage(tmp_path)) as conn:
        conn.execute(
            """
            update auth_recovery_incidents
            set status = 'verifying',
              verification_started_at = '2026-07-26T00:00:00Z'
            where incident_id = ?
            """,
            (incident["incident_id"],),
        )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(status=AccountStatus.VERIFIED),
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: tmp_path / "setup-state.json",
    )
    from backend.application.workflows.auth_recovery import verify_auth_recovery

    payload = verify_auth_recovery(
        tmp_path,
        incident["incident_id"],
        checked_at="2026-07-26T01:10:00Z",
    )
    assert payload["ok"] is True


def test_verification_claim_token_fences_stale_owner(tmp_path) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    repo = AuthRecoveryRepository(tmp_path)
    owner_a = repo.begin_verification(
        incident["incident_id"],
        updated_at="2026-07-26T01:00:00Z",
        stale_before="2026-07-26T00:58:00Z",
    )
    owner_b = repo.begin_verification(
        incident["incident_id"],
        updated_at="2026-07-26T01:03:00Z",
        stale_before="2026-07-26T01:01:00Z",
    )
    token_a = owner_a["verification_claim_token"]
    token_b = owner_b["verification_claim_token"]
    assert token_a != token_b

    assert repo.verification_failed(
        incident["incident_id"],
        claim_token=token_a,
        error_code="platform_session_not_ready",
        updated_at="2026-07-26T01:03:01Z",
    ) is None
    still_b = repo.get(incident["incident_id"])
    assert still_b["status"] == "verifying"
    assert still_b["verification_claim_token"] == token_b
    outcome_a, _ = resolve_verified_auth_recovery(
        repo,
        incident["incident_id"],
        claim_token=token_a,
        platform="x",
        outage_epoch=1,
        occurred_at="2026-07-26T01:03:02Z",
    )
    assert outcome_a == "superseded"
    assert RuntimeCircuitRepository(tmp_path).get("platform", "x")["state"] == "open"
    assert TaskRepository.for_base_dir(tmp_path).get("fetch-one").status is TaskStatus.BLOCKED

    outcome_b, resolved = resolve_verified_auth_recovery(
        repo,
        incident["incident_id"],
        claim_token=token_b,
        platform="x",
        outage_epoch=1,
        occurred_at="2026-07-26T01:03:03Z",
    )
    assert outcome_b == "resolved"
    assert resolved["status"] == "resolved"


def test_stale_verifier_cannot_write_verified_setup_projection(
    tmp_path, monkeypatch
) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    setup_path = tmp_path / "setup-state.json"
    update_account_state(
        setup_path,
        "x",
        lambda previous: AccountState(
            status=AccountStatus.NEEDS_LOGIN,
            persisted=False,
            raw=dict(previous.raw),
        ),
    )
    original_setup = setup_path.read_text()
    probe_started = threading.Event()
    allow_probe = threading.Event()
    errors: list[BaseException] = []

    def delayed_probe(platform: str) -> AccountState:
        probe_started.set()
        assert allow_probe.wait(timeout=3)
        return AccountState(status=AccountStatus.VERIFIED)

    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        delayed_probe,
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: setup_path,
    )
    from backend.application.workflows.auth_recovery import verify_auth_recovery

    def verify_as_owner_a() -> None:
        try:
            verify_auth_recovery(
                tmp_path,
                incident["incident_id"],
                checked_at="2026-07-26T01:00:00Z",
            )
        except BaseException as exc:
            errors.append(exc)

    owner_a = threading.Thread(target=verify_as_owner_a)
    owner_a.start()
    assert probe_started.wait(timeout=3)
    owner_b = AuthRecoveryRepository(tmp_path).begin_verification(
        incident["incident_id"],
        updated_at="2026-07-26T01:03:00Z",
        stale_before="2026-07-26T01:01:00Z",
    )
    assert owner_b is not None
    allow_probe.set()
    owner_a.join(timeout=3)

    assert not owner_a.is_alive()
    assert len(errors) == 1
    assert errors[0].__class__.__name__ == "AuthRecoverySuperseded"
    assert setup_path.read_text() == original_setup


def test_old_epoch_returns_recovery_superseded(tmp_path, monkeypatch) -> None:
    incident = _incident(tmp_path)
    _block_task(tmp_path, "fetch-one")
    setup_path = tmp_path / "setup-state.json"
    update_account_state(
        setup_path,
        "x",
        lambda previous: AccountState(
            status=AccountStatus.NEEDS_LOGIN,
            persisted=False,
            raw=dict(previous.raw),
        ),
    )
    original_setup = setup_path.read_text()
    with sqlite3.connect(initialize_runtime_storage(tmp_path)) as conn:
        conn.execute(
            "update runtime_circuits set outage_epoch = outage_epoch + 1 where scope_kind = 'platform' and scope_key = 'x'"
        )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.probe_platform",
        lambda platform: AccountState(status=AccountStatus.VERIFIED),
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: setup_path,
    )
    response = _client(tmp_path).post(
        f"/api/auth-recoveries/{incident['incident_id']}/verify"
    )
    assert response.status_code == 409
    assert response.json()["code"] == "recovery_superseded"
    assert TaskRepository.for_base_dir(tmp_path).get("fetch-one").status is TaskStatus.BLOCKED
    assert setup_path.read_text() == original_setup


def _capture_outbox(tmp_path, monkeypatch, sends):
    from backend.application.usecases.notifications import enqueue_notification
    from backend.infrastructure.notifications import rules
    from backend.infrastructure.notifications.config import load_config, save_config
    monkeypatch.setattr(rules, 'DEFAULT_CONFIG_PATH', tmp_path / 'notifications.json')
    config = load_config()
    config['enabled'] = True
    config['email'].update(username='fixture', password='fixture-only', from_email='a@example.test', to_email='b@example.test')
    save_config(config)

    def capture(base_dir, **kwargs):
        sends.append(kwargs['related'][1])
        return enqueue_notification(base_dir, **kwargs)
    monkeypatch.setattr('backend.application.workflows.auth_recovery.enqueue_notification', capture)
