-- Normalize only disabled historical subscriptions; preserve source identity,
-- RawItem/card payloads, and immutable source_config_versions with their hashes.
update sources
set raw_metadata = json_remove(raw_metadata, '$.feed_provider', '$.feed_content_mode'),
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
where platform = 'rss' and source_type = 'feed' and enabled = 0
  and json_extract(raw_metadata, '$.feed_provider') = 'werss';

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
where platform = 'wechat' and status != 'resolved';

update auth_qr_rounds
set status = 'resolved', tab_lease_id = null, code_expires_at = null,
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
where incident_id in (select incident_id from auth_recovery_incidents where platform = 'wechat')
  and status != 'resolved';

update tasks
set status = 'skipped', error_reason = 'feature_retired',
    claimed_by = null, lease_expires_at = null, heartbeat_at = null,
    finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
where task_type in ('auth_qr_capture', 'auth_qr_check') and target_kind = 'auth_incident'
  and target_id in (select incident_id from auth_recovery_incidents where platform = 'wechat')
  and status not in ('success', 'skipped');

update notification_outbox
set status = 'skipped', last_error_code = 'platform_retired',
    claim_token = null, lease_until = null,
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
where related_kind = 'auth_incident'
  and related_id in (select incident_id from auth_recovery_incidents where platform = 'wechat')
  and status in ('pending', 'sending', 'failed');

update runtime_circuits
set state = 'closed', recovery_mode = 'automatic', reason_code = null,
    user_action = null, cooldown_until = null, probe_owner_token = null,
    probe_acquired_at = null, probe_lease_expires_at = null,
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
where scope_kind = 'platform' and scope_key = 'wechat';
