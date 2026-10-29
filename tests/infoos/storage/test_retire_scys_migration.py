"""v18 retires only legacy scys auth state in one migration transaction."""
import sqlite3

import pytest

from backend.infrastructure.storage.migrations import runner

OLD = '2026-08-07T01:00:00Z'


def _rows(conn, table):
    return [tuple(row) for row in conn.execute(f'select * from {table} order by rowid')]


def test_v17_upgrade_retires_scys_and_preserves_other_platforms(tmp_path):
    def interrupt(event):
        if event['migration'] == 18:
            raise RuntimeError('interrupted')

    path = tmp_path / 'retire-scys.sqlite3'
    with pytest.raises(RuntimeError, match='interrupted'):
        runner.migrate_database(path, progress_callback=interrupt)
    with sqlite3.connect(path) as conn:
        for incident_id, platform, status, epoch in [
            ('scys-open', 'scys', 'awaiting_user', 1),
            ('scys-verifying', 'scys', 'verifying', 2),
            ('scys-resolved', 'scys', 'resolved', 3),
            ('zsxq-open', 'zsxq', 'awaiting_user', 1),
        ]:
            conn.execute('''insert into auth_recovery_incidents (
                incident_id, platform, outage_epoch, reason_code, status, created_at, updated_at,
                resolved_at, verification_started_at, verification_claim_token,
                notification_status, notification_claim_token, notification_lease_until
            ) values (?, ?, ?, 'platform_not_logged_in', ?, ?, ?, ?, ?, 'verify-claim',
                'pending', 'notify-claim', ?)''',
                (incident_id, platform, epoch, status, OLD, OLD, OLD if status == 'resolved' else None, OLD, OLD))
        for scope, key in [('platform', 'scys'), ('platform', 'zsxq'), ('provider', 'scys')]:
            conn.execute('''insert into runtime_circuits (
                scope_kind, scope_key, state, recovery_mode, outage_epoch, reason_code,
                user_action, cooldown_until, probe_owner_token, probe_acquired_at,
                probe_lease_expires_at, updated_at
            ) values (?, ?, 'open', 'manual', 3, 'platform_not_logged_in',
                'login_or_config_required', ?, 'probe-claim', ?, ?, ?)''', (scope, key, OLD, OLD, OLD, OLD))
        before_incidents = _rows(conn, 'auth_recovery_incidents')
        before_circuits = _rows(conn, 'runtime_circuits')

    with pytest.raises(RuntimeError, match='interrupted'):
        runner.migrate_database(path, progress_callback=interrupt)
    with sqlite3.connect(path) as conn:
        assert conn.execute('pragma user_version').fetchone()[0] == 17
        assert _rows(conn, 'auth_recovery_incidents') == before_incidents
        assert _rows(conn, 'runtime_circuits') == before_circuits

    def stop_after_v18(event):
        if event['migration'] == 19:
            raise RuntimeError('stop after v18')

    with pytest.raises(RuntimeError, match='stop after v18'):
        runner.migrate_database(path, progress_callback=stop_after_v18)
    with sqlite3.connect(path) as conn:
        assert conn.execute('pragma user_version').fetchone()[0] == 18
        conn.row_factory = sqlite3.Row
        incidents = conn.execute('select * from auth_recovery_incidents order by rowid').fetchall()
        for row in incidents[:2]:
            assert row['status'] == 'resolved'
            assert row['last_error_code'] == 'platform_retired'
            assert row['notification_status'] == 'skipped'
            assert row['resolved_at'] == row['updated_at'] > OLD
            for field in ['verification_started_at', 'verification_claim_token',
                          'notification_claim_token', 'notification_lease_until']:
                assert row[field] is None
        assert [tuple(row) for row in incidents[2:]] == before_incidents[2:]
        circuits = conn.execute('select * from runtime_circuits order by rowid').fetchall()
        scys = circuits[0]
        assert scys['state'] == 'closed'
        assert scys['recovery_mode'] == 'automatic'
        assert scys['outage_epoch'] == 3
        assert scys['updated_at'] > OLD
        for field in ['reason_code', 'user_action', 'cooldown_until', 'probe_owner_token',
                      'probe_acquired_at', 'probe_lease_expires_at']:
            assert scys[field] is None
        assert [tuple(row) for row in circuits[1:]] == before_circuits[1:]
        after_incidents = _rows(conn, 'auth_recovery_incidents')
        after_circuits = _rows(conn, 'runtime_circuits')
        assert conn.execute('pragma integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('pragma foreign_key_check').fetchall() == []
    with pytest.raises(RuntimeError, match='stop after v18'):
        runner.migrate_database(path, progress_callback=stop_after_v18)
    with sqlite3.connect(path) as conn:
        assert conn.execute('pragma user_version').fetchone()[0] == 18
        assert _rows(conn, 'auth_recovery_incidents') == after_incidents
        assert _rows(conn, 'runtime_circuits') == after_circuits


def test_fresh_database_reaches_v18_without_auth_state(tmp_path):
    path = tmp_path / 'fresh.sqlite3'
    assert runner.migrate_database(path).after_version == runner.LATEST_VERSION
    with sqlite3.connect(path) as conn:
        assert _rows(conn, 'auth_recovery_incidents') == []
        assert _rows(conn, 'runtime_circuits') == []
