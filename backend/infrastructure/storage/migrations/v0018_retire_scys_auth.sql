update auth_recovery_incidents
set status = 'resolved',
    resolved_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
    last_error_code = 'platform_retired',
    notification_status = 'skipped',
    verification_started_at = null,
    verification_claim_token = null,
    notification_claim_token = null,
    notification_lease_until = null
where platform = 'scys' and status != 'resolved';

update runtime_circuits
set state = 'closed',
    recovery_mode = 'automatic',
    reason_code = null,
    user_action = null,
    cooldown_until = null,
    probe_owner_token = null,
    probe_acquired_at = null,
    probe_lease_expires_at = null,
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
where scope_kind = 'platform' and scope_key = 'scys';
