pragma foreign_keys = on;
create table if not exists domains (
  domain_id text primary key,
  name text not null unique,
  sort_order integer not null default 0,
  hidden integer not null default 0,
  created_at text not null,
  updated_at text not null
);
create table if not exists sources (
  source_id text primary key,
  source_scope text not null default 'maintained',
  platform text not null,
  source_type text not null,
  identifier text not null,
  display_name text not null,
  default_domain_id text references domains(domain_id),
  cadence_hours real not null,
  notes text,
  enabled integer not null default 1,
  connector_status text not null,
  connector_id text,
  raw_metadata text not null default '{}',
  calibration_input_version text,
  next_run_at text,
  last_run_at text,
  created_at text not null,
  updated_at text not null
);
alter table sources add column calibration_input_version text;
alter table sources add column source_scope text not null default 'maintained';
create index if not exists idx_sources_scope_enabled on sources(source_scope, enabled, platform);
create table if not exists source_cursors (
  source_id text primary key references sources(source_id),
  connector_id text not null,
  cursor_value text,
  last_run_id text,
  updated_at text not null
);
create unique index if not exists idx_source_cursors_connector on source_cursors(connector_id);
create table if not exists run_history (
  run_id text primary key,
  source_id text not null references sources(source_id),
  connector_id text,
  scheduled_for text not null,
  started_at text not null,
  finished_at text not null,
  status text not null,
  fetched_count integer not null default 0,
  visible_count integer not null default 0,
  new_count integer not null default 0,
  skipped_count integer not null default 0,
  dedup_skipped_count integer not null default 0,
  error_count integer not null default 0,
  duration_ms integer not null default 0,
  next_run_at text,
  attempt integer not null default 1,
  trigger text not null,
  decision text,
  error_summary text,
  raw_payload text not null default '{}',
  task_type text not null default 'fetch',
  target_kind text,
  target_id text,
  artifacts_summary text not null default '{}',
  derived_task_ids text not null default '[]'
);
create index if not exists idx_run_history_task_type on run_history(task_type, finished_at desc);
create index if not exists idx_run_history_target on run_history(target_kind, target_id);

create table if not exists fetch_rate_limit_state (
  throttle_key text primary key,
  platform text not null,
  identity_id text not null,
  next_available_at text,
  hour_window_start text,
  hour_count integer not null default 0,
  day_window_start text,
  day_count integer not null default 0,
  updated_at text not null
);

-- migration: add columns that may be missing from older databases
alter table run_history add column task_type text not null default 'fetch';
alter table run_history add column target_kind text;
alter table run_history add column target_id text;
alter table run_history add column artifacts_summary text not null default '{}';
alter table run_history add column derived_task_ids text not null default '[]';
alter table run_history add column visible_count integer not null default 0;
alter table run_history add column dedup_skipped_count integer not null default 0;

create table if not exists raw_items (
  connector_id text not null,
  raw_item_id text not null,
  source_id text,
  run_id text,
  source_platform text not null,
  source_type text not null,
  source_url text not null,
  source_author text,
  title text not null default '',
  raw_text text,
  captured_at text not null,
  published_at text,
  snapshot_ref text,
  raw_payload text not null default '{}',
  first_seen_at text not null,
  last_seen_at text not null,
  primary key (connector_id, raw_item_id)
);

create table if not exists cards (
  card_id text primary key,
  card_type text not null default 'information' check (card_type = 'information'),
  version integer not null default 0 check (version >= 0),
  content_hash text,
  updated_at text not null default '',
  status text not null default 'processing' check (status in ('processing', 'active', 'withdrawn')),
  completeness_status text not null default 'processing' check (completeness_status in ('processing', 'complete', 'partial')),
  missing_reasons text not null default '[]',
  processor_version text,
  raw_connector_id text,
  raw_item_id text,
  source_run_id text,
  source_id text references sources(source_id),
  source_platform text not null,
  source_type text not null,
  source_url text not null,
  source_author text,
  title text not null,
  content text not null,
  summary text not null default '',
  primary_domain_id text references domains(domain_id),
  secondary_domain_ids text not null default '[]',
  published_at text,
  captured_at text not null,
  processed_at text not null,
  workflow_status text not null default 'new',
  ignored integer not null default 0,
  skimmed integer not null default 0,
  raw_payload text not null default '{}'
); -- migration: stable information-card delivery contract for existing databases
alter table cards add column card_type text not null default 'information';
alter table cards add column version integer not null default 0;
alter table cards add column content_hash text;
alter table cards add column updated_at text not null default '';
alter table cards add column status text not null default 'processing';
alter table cards add column completeness_status text not null default 'processing';
alter table cards add column missing_reasons text not null default '[]';
alter table cards add column processor_version text;
alter table cards add column raw_connector_id text;
alter table cards add column raw_item_id text;
alter table cards add column source_run_id text;
create index if not exists idx_cards_active_updated
  on cards(status, updated_at, card_id)
  where status = 'active' and version >= 1 and content_hash is not null;

create table if not exists card_content_blocks (
  block_id text primary key,
  card_id text not null references cards(card_id),
  position integer not null default 0,
  layer text not null,
  block_kind text not null,
  status text not null,
  body text not null default '',
  segments text not null default '[]',
  source_url text,
  provenance text not null default '',
  raw_payload text not null default '{}',
  created_at text not null,
  updated_at text not null
);
-- migration: add columns that may be missing from older databases
alter table card_content_blocks add column position integer not null default 0;
create index if not exists idx_card_content_blocks_card on card_content_blocks(card_id, layer, block_kind);
create table if not exists card_assets (
  asset_id text primary key,
  card_id text not null references cards(card_id),
  asset_kind text not null,
  source_url text not null,
  remote_url text,
  local_path text,
  mime_type text,
  size_bytes integer,
  content_hash text,
  preservation_class text not null default 'cached',
  status text not null,
  cleanup_state text not null default 'present',
  raw_payload text not null default '{}',
  created_at text not null,
  updated_at text not null
);
create index if not exists idx_card_assets_card on card_assets(card_id, asset_kind, status);
-- migration: add columns that may be missing from older databases
alter table card_assets add column content_hash text;
create index if not exists idx_card_assets_content_hash on card_assets(content_hash) where content_hash is not null;

create table if not exists topics (
  topic_id text primary key,
  title text not null,
  latest_progress text not null default '',
  ai_interpretation text not null default '',
  primary_domain_id text references domains(domain_id),
  secondary_domain_ids text not null default '[]',
  status text not null default '刚出现',
  updated_at text not null,
  created_at text not null,
  source_summary text not null default '',
  raw_payload text not null default '{}'
);

create table if not exists topic_cards (
  topic_id text not null,
  card_id text not null,
  position integer not null default 0,
  created_at text not null,
  primary key (topic_id, card_id)
);

create table if not exists card_ai_insights (
  card_id text primary key,
  main_point text not null,
  topic_assignment text,
  scores text not null default '{}',
  score_reasons text not null default '{}',
  why_it_matters text not null default '',
  is_time_sensitive integer not null default 0,
  evidence_quotes text not null default '[]',
  status text not null default 'succeeded',
  last_error text,
  model text,
  prompt_version text,
  input_version text,
  generated_at text not null
);
alter table card_ai_insights add column input_version text;

-- ``card_ai_insights`` remains the latest-insight projection used by read
-- models. Durable pipeline stages must instead retain each successful SUMMARY
-- product so a queued semantic task cannot read a newer replacement summary.
create table if not exists card_ai_insight_versions (
  card_id text not null,
  input_version text not null,
  main_point text not null,
  topic_assignment text,
  scores text not null default '{}',
  score_reasons text not null default '{}',
  why_it_matters text not null default '',
  is_time_sensitive integer not null default 0,
  evidence_quotes text not null default '[]',
  status text not null default 'succeeded',
  last_error text,
  model text,
  prompt_version text,
  generated_at text not null,
  primary key (card_id, input_version)
);

create table if not exists topic_ai_insights (
  topic_id text primary key,
  core_event text not null default '',
  why_it_matters text not null default '',
  timeline text not null default '[]',
  key_points text not null default '[]',
  scores text not null default '{}',
  model text,
  prompt_version text,
  generated_at text not null
);

create table if not exists llm_jobs (
  job_id text primary key,
  target_type text not null,
  target_id text not null,
  status text not null,
  priority integer not null default 50,
  attempts integer not null default 0,
  next_run_at text not null,
  last_error text,
  created_at text not null,
  updated_at text not null
);

create table if not exists notifications (
  notification_id text primary key,
  created_at text not null,
  kind text not null,
  severity text not null,
  title text not null,
  body text not null,
  source_id text,
  topic_id text,
  card_id text,
  run_id text,
  delivered_to text not null default '[]',
  cooldown_key text,
  details text not null default '{}',
  read_at text
);
-- migration: add columns that may be missing from older databases
alter table notifications add column read_at text;

create table if not exists user_actions (
  action_id text primary key,
  card_id text references cards(card_id),
  topic_id text references topics(topic_id),
  decision_id text,
  artifact_id text,
  action text not null,
  status text not null default 'recorded',
  created_at text not null,
  updated_at text,
  details text not null default '{}'
);
-- migration: user actions gained durable decision/artifact status metadata.
alter table user_actions add column decision_id text;
alter table user_actions add column artifact_id text;
alter table user_actions add column status text not null default 'recorded';
alter table user_actions add column updated_at text;
create index if not exists idx_user_actions_card_created on user_actions(card_id, created_at desc);
create index if not exists idx_user_actions_topic_created on user_actions(topic_id, created_at desc);
create index if not exists idx_user_actions_decision on user_actions(decision_id, created_at desc);

create table if not exists user_comments (
  comment_id text primary key,
  card_id text not null references cards(card_id),
  body text not null,
  created_at text not null,
  updated_at text not null
);

create table if not exists reading_queue (
  card_id text primary key references cards(card_id),
  added_at text not null,
  status text not null default 'pending'
);

create table if not exists materials (
  material_id text primary key,
  card_id text not null references cards(card_id),
  file_path text not null,
  title text not null,
  exported_at text not null,
  updated_at text not null
);

create table if not exists topic_writing_context_exports (
  export_id text primary key,
  topic_id text not null references topics(topic_id),
  file_path text not null,
  title text not null,
  exported_at text not null,
  updated_at text not null,
  card_count integer not null default 0,
  raw_payload text not null default '{}'
);

create table if not exists settings (
  key text primary key,
  value text not null,
  updated_at text not null
);

create virtual table if not exists infoos_search using fts5(
  entity_type,
  entity_id,
  title,
  body,
  source
);
create table if not exists tasks (
  task_id text primary key,
  task_type text not null,
  target_kind text not null,
  target_id text not null,
  status text not null,
  depends_on text not null default '[]',
  attempt integer not null default 1,
  max_attempts integer not null default 3,
  next_run_at text,
  error_reason text,
  artifacts_summary text not null default '{}',
  claimed_by text,
  lease_expires_at text,
  heartbeat_at text,
  started_at text,
  finished_at text,
  input_version text not null default 'v1',
  result_version text,
  last_failure_class text,
  completion_token text,
  queue_name text, priority integer, deadline_at text,
  created_at text not null,
  updated_at text not null
);
-- migration: add task ownership and lease columns that may be missing from older databases
alter table tasks add column claimed_by text;
alter table tasks add column lease_expires_at text;
alter table tasks add column heartbeat_at text;
alter table tasks add column started_at text;
alter table tasks add column finished_at text;
alter table tasks add column input_version text not null default 'v1';
alter table tasks add column result_version text;
alter table tasks add column last_failure_class text;
alter table tasks add column completion_token text;
alter table tasks add column queue_name text;
alter table tasks add column priority integer;
alter table tasks add column deadline_at text;
create index if not exists idx_tasks_status on tasks(status, created_at);
create index if not exists idx_tasks_target on tasks(target_kind, target_id);
create index if not exists idx_tasks_due on tasks(status, next_run_at, created_at);
create index if not exists idx_tasks_lease on tasks(status, lease_expires_at);
create index if not exists idx_tasks_queue_due on tasks(status, queue_name, priority, deadline_at, next_run_at, created_at);
create table if not exists task_events (
  event_id text primary key,
  task_id text,
  chain_id text,
  event_type text not null,
  status text,
  failure_class text,
  payload text not null default '{}',
  occurred_at text not null
);
create index if not exists idx_task_events_task
  on task_events(task_id, occurred_at);
create index if not exists idx_task_events_chain
  on task_events(chain_id, occurred_at);
create index if not exists idx_task_events_type_time
  on task_events(event_type, occurred_at);

create table if not exists source_calibrations (
  calibration_id text primary key,
  source_id text not null references sources(source_id),
  platform text not null,
  source_type text not null,
  identifier text not null,
  result text not null,
  visible_count integer not null default 0,
  sample_count integer not null default 0,
  failure_class text,
  error_summary text,
  task_id text not null,
  attempt integer not null default 1,
  calibrated_at text not null,
  raw_payload text not null default '{}'
);
create index if not exists idx_source_calibrations_source_time
  on source_calibrations(source_id, calibrated_at);
create index if not exists idx_source_calibrations_platform_time
  on source_calibrations(platform, source_type, calibrated_at);
alter table source_calibrations add column attempt integer not null default 1;
drop index if exists idx_source_calibrations_task;
create unique index if not exists idx_source_calibrations_task_attempt
  on source_calibrations(task_id, attempt);

create table if not exists acquisition_checkpoints (
  task_id text primary key references tasks(task_id),
  source_id text not null references sources(source_id),
  run_id text not null,
  source_input_version text not null,
  initial_fetch integer not null default 0,
  calibration_evidence_id text,
  committed_last_run_at text,
  committed_next_run_at text,
  payload text not null default '{}',
  committed_at text not null
);
create index if not exists idx_acquisition_checkpoints_source_version
  on acquisition_checkpoints(source_id, source_input_version);

create table if not exists task_batches (
  batch_id text primary key,
  batch_type text not null,
  status text not null default 'queued',
  source_ids text not null default '[]',
  metadata text not null default '{}',
  created_at text not null,
  updated_at text not null,
  started_at text,
  finished_at text
);

create table if not exists task_batch_items (
  batch_id text not null references task_batches(batch_id),
  task_id text not null,
  created_at text not null,
  primary key (batch_id, task_id)
);
create index if not exists idx_task_batch_items_task_id
  on task_batch_items(task_id);

-- Plan D: events table (spec §4)
create table if not exists events (
  event_id text primary key,
  title text not null,
  summary text not null default '',
  first_seen_at text not null,
  last_updated_at text not null,
  status text not null default 'open',  -- open | merged | archived
  topic_ids text not null default '[]',
  cluster_method text not null default 'heuristic',
  card_count integer not null default 0,
  source_count integer not null default 0,
  raw_payload text not null default '{}',
  created_at text not null,
  updated_at text not null
);
create index if not exists idx_events_status_updated on events(status, last_updated_at desc);

create table if not exists event_cards (
  event_id text not null references events(event_id),
  card_id text not null references cards(card_id),
  relevance_score real not null default 1.0,
  joined_at text not null,
  primary key (event_id, card_id)
);
create index if not exists idx_event_cards_card on event_cards(card_id);

create table if not exists semantic_annotations (
  annotation_id text primary key,
  target_kind text not null,
  target_id text not null,
  labels text not null default '[]',
  entities text not null default '[]',
  insight text not null default '',
  confidence real not null default 0.0,
  evidence_refs text not null default '[]',
  model text,
  prompt_version text,
  generated_at text not null
);
create index if not exists idx_semantic_annotations_target on semantic_annotations(target_kind, target_id, generated_at desc);

create table if not exists value_scores (
  score_id text primary key,
  target_kind text not null,
  target_id text not null,
  scores text not null default '{}',
  score_reasons text not null default '{}',
  generated_at text not null
);
create index if not exists idx_value_scores_target on value_scores(target_kind, target_id, generated_at desc);

create table if not exists decision_records (
  decision_id text primary key,
  target_kind text not null,
  target_id text not null,
  route text not null,
  reason text not null default '',
  evidence_refs text not null default '[]',
  status text not null default 'open',
  created_at text not null,
  updated_at text not null,
  status_updated_at text
);
-- A status click is operational history, not a regenerated decision product.
-- Keep the two clocks separate so product read models retain version ordering.
alter table decision_records add column status_updated_at text;
create index if not exists idx_decision_records_route on decision_records(route, status, updated_at desc);
create index if not exists idx_decision_records_target on decision_records(target_kind, target_id, updated_at desc);

create table if not exists output_artifacts (
  artifact_id text primary key,
  artifact_type text not null,
  source_decision_id text references decision_records(decision_id),
  title text not null default '',
  body text not null default '{}',
  status text not null default 'draft',
  created_at text not null,
  updated_at text not null
);
create index if not exists idx_output_artifacts_decision on output_artifacts(source_decision_id, artifact_type, updated_at desc);

create table if not exists interpretation_profiles (
  profile_id text primary key,
  name text not null,
  scope text not null,
  platform text,
  source_group_id text,
  source_id text,
  model text not null default '',
  prompt text not null,
  version text not null,
  enabled integer not null default 1,
  created_at text not null,
  updated_at text not null
);
alter table interpretation_profiles add column model text not null default '';
create index if not exists idx_interpretation_profiles_scope
  on interpretation_profiles(scope, platform, source_group_id, source_id, enabled);

create table if not exists source_interpretation_profile_bindings (
  binding_id text primary key,
  source_id text not null references sources(source_id),
  profile_id text not null references interpretation_profiles(profile_id),
  created_at text not null,
  updated_at text not null
);
create unique index if not exists idx_source_interpretation_bindings_source
  on source_interpretation_profile_bindings(source_id);
