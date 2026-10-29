from __future__ import annotations

import logging

from backend.application.workflows import auth_recovery


def test_committed_auth_followups_log_failures_and_continue(tmp_path, monkeypatch, caplog) -> None:
    def fail_mark(*_args, **_kwargs):
        raise RuntimeError("account failed")

    def fail_notification(*_args, **_kwargs):
        raise RuntimeError("notification failed")

    monkeypatch.setattr(auth_recovery, "mark_account_needs_login", fail_mark)
    monkeypatch.setattr(auth_recovery, "attempt_auth_notification", fail_notification)
    with caplog.at_level(logging.ERROR):
        auth_recovery.complete_committed_auth_failure(
            tmp_path, incident_id="incident-1", platform="x", occurred_at="2026-09-22T00:00:00Z",
        )
    assert "incident-1" in caplog.text
    assert "auth_recovery_account_mark_failed" in caplog.text
    assert "auth_recovery_notification_failed" in caplog.text
