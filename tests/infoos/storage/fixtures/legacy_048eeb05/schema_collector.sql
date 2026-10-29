-- Additive schema owned by the focused collector product surface.
-- Loaded after schema.sql so foreign keys can reference the legacy base tables.
create table if not exists source_config_versions (
  version_id text primary key,
  source_id text not null references sources(source_id),
  config_version text not null,
  fetch_input_version text not null,
  config_json text not null,
  change_reason text not null,
  created_at text not null,
  unique(source_id, config_version)
);
create index if not exists idx_source_config_versions_source_created
  on source_config_versions(source_id, created_at desc);

create table if not exists api_tokens (
  token_id text primary key,
  name text not null,
  token_prefix text not null,
  token_digest text not null unique,
  scopes text not null,
  created_at text not null,
  revoked_at text,
  last_used_at text,
  replaced_by_token_id text,
  webhook_enabled integer not null default 0,
  webhook_url text,
  config_revision integer not null default 1,
  webhook_config_generation integer not null default 1,
  webhook_delivery_status text not null default 'inactive',
  webhook_consecutive_failures integer not null default 0,
  webhook_last_attempt_at text,
  webhook_last_success_at text,
  webhook_last_failure_at text,
  webhook_last_error text,
  webhook_circuit_opened_at text,
  updated_at text
);
alter table api_tokens add column webhook_enabled integer not null default 0;
alter table api_tokens add column webhook_url text;
alter table api_tokens add column config_revision integer not null default 1;
alter table api_tokens add column webhook_config_generation integer not null default 1;
alter table api_tokens add column webhook_delivery_status text not null default 'inactive';
alter table api_tokens add column webhook_consecutive_failures integer not null default 0;
alter table api_tokens add column webhook_last_attempt_at text;
alter table api_tokens add column webhook_last_success_at text;
alter table api_tokens add column webhook_last_failure_at text;
alter table api_tokens add column webhook_last_error text;
alter table api_tokens add column webhook_circuit_opened_at text;
alter table api_tokens add column updated_at text;
update api_tokens set updated_at = coalesce(updated_at, created_at);
update api_tokens set webhook_delivery_status = case
  when webhook_enabled = 1 and webhook_url is not null
    and webhook_delivery_status = 'inactive' then 'active'
  when webhook_enabled = 0 or webhook_url is null then 'inactive'
  else webhook_delivery_status end;
create index if not exists idx_api_tokens_created on api_tokens(created_at desc);

create table if not exists api_request_logs (
  request_id text primary key,
  token_id text,
  token_prefix text,
  client_label text,
  method text not null,
  path text not null,
  status_code integer not null,
  duration_ms integer not null,
  response_bytes integer,
  result_code text not null,
  result_summary text not null,
  response_meta text not null default '{}',
  created_at text not null
);
create index if not exists idx_api_request_logs_created
  on api_request_logs(created_at desc);
create index if not exists idx_api_request_logs_status_created
  on api_request_logs(status_code, created_at desc);
create index if not exists idx_api_request_logs_token_created
  on api_request_logs(token_id, created_at desc);

create table if not exists capture_receipts (
  capture_id text primary key,
  client_capture_id text not null,
  token_id text not null references api_tokens(token_id),
  payload_hash text not null,
  canonical_identity_hash text not null,
  content_identity_hash text,
  observation_version integer not null,
  capture_mode text not null check (capture_mode in ('url_only', 'readable_content')),
  disposition text not null
    check (disposition in ('new', 'refresh', 'unchanged', 'idempotent_replay')),
  processing_status text not null
    check (processing_status in ('pending', 'running', 'succeeded', 'failed')),
  canonical_url text not null,
  source_id text not null references sources(source_id),
  run_id text not null references run_history(run_id),
  raw_connector_id text not null,
  raw_item_id text not null,
  task_id text references tasks(task_id),
  card_id text references cards(card_id),
  error_code text,
  created_at text not null,
  updated_at text not null,
  unique(token_id, client_capture_id),
  unique(canonical_identity_hash, observation_version)
);
create index if not exists idx_capture_receipts_identity_created
  on capture_receipts(canonical_identity_hash, created_at desc);
create index if not exists idx_capture_receipts_task
  on capture_receipts(task_id);
create index if not exists idx_capture_receipts_card
  on capture_receipts(card_id);

create table if not exists runtime_resource_leases (
  resource_kind text not null,
  resource_key text not null,
  owner_token text not null unique,
  task_id text not null,
  acquired_at text not null,
  lease_expires_at text not null,
  updated_at text not null,
  primary key (resource_kind, resource_key)
);
create index if not exists idx_runtime_resource_leases_expiry
  on runtime_resource_leases(lease_expires_at);
create index if not exists idx_runtime_resource_leases_task
  on runtime_resource_leases(task_id);

create table if not exists runtime_circuits (
  scope_kind text not null check (scope_kind in ('platform', 'provider')),
  scope_key text not null,
  state text not null default 'closed'
    check (state in ('closed', 'open', 'half_open')),
  recovery_mode text not null default 'automatic'
    check (recovery_mode in ('automatic', 'manual')),
  outage_epoch integer not null default 0,
  consecutive_failures integer not null default 0,
  opened_at text,
  cooldown_until text,
  last_failure_at text,
  last_success_at text,
  reason_code text,
  user_action text,
  probe_owner_token text,
  probe_acquired_at text,
  probe_lease_expires_at text,
  updated_at text not null,
  primary key (scope_kind, scope_key)
);
create index if not exists idx_runtime_circuits_state_cooldown
  on runtime_circuits(state, cooldown_until);
alter table runtime_circuits add column outage_epoch integer not null default 0;

create table if not exists auth_recovery_incidents (
  incident_id text primary key,
  platform text not null,
  outage_epoch integer not null,
  reason_code text not null,
  status text not null
    check (status in ('awaiting_user', 'verifying', 'resolved')),
  created_at text not null,
  updated_at text not null,
  resolved_at text,
  verification_started_at text,
  verification_claim_token text,
  last_error_code text,
  notification_status text not null default 'pending'
    check (notification_status in ('pending', 'sent', 'skipped', 'failed')),
  notified_at text,
  notification_attempts integer not null default 0,
  notification_lease_until text,
  notification_claim_token text,
  recovered_task_count integer not null default 0,
  unique(platform, outage_epoch)
);
alter table auth_recovery_incidents add column verification_started_at text;
alter table auth_recovery_incidents add column verification_claim_token text;
alter table auth_recovery_incidents add column notification_attempts integer not null default 0;
alter table auth_recovery_incidents add column notification_lease_until text;
alter table auth_recovery_incidents add column notification_claim_token text;
create index if not exists idx_auth_recovery_incidents_status_updated
  on auth_recovery_incidents(status, updated_at);

create table if not exists auth_recovery_incident_tasks (
  incident_id text not null references auth_recovery_incidents(incident_id),
  task_id text not null references tasks(task_id),
  created_at text not null,
  primary key (incident_id, task_id)
);
create index if not exists idx_auth_recovery_incident_tasks_task
  on auth_recovery_incident_tasks(task_id);

create index if not exists idx_task_events_occurred_at on task_events(occurred_at);
