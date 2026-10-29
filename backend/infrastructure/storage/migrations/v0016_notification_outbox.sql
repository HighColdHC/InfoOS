create table notification_outbox (
  notification_id text primary key,
  kind text not null,
  dedupe_key text not null unique,
  channel text not null,
  status text not null check(status in ('pending','sending','sent','failed','skipped','expired')),
  title text not null,
  body text not null,
  image_path text,
  related_kind text,
  related_id text,
  quiet_allowed integer not null default 0,
  attempts integer not null default 0,
  next_attempt_at text not null,
  expires_at text not null,
  claim_token text,
  lease_until text,
  provider_message_id text,
  last_error_code text,
  created_at text not null,
  updated_at text not null,
  sent_at text
);
create index idx_notification_outbox_due on notification_outbox(status,next_attempt_at);
create index idx_notification_outbox_related on notification_outbox(related_kind,related_id);
create table auth_qr_rounds (
  incident_id text primary key references auth_recovery_incidents(incident_id),
  round_no integer not null default 0,
  status text not null default 'idle' check(status in ('idle','capturing','waiting','closing','resolved')),
  codes_sent integer not null default 0,
  code_expires_at text,
  next_round_at text not null,
  day text not null,
  rounds_today integer not null default 0,
  tab_site text not null,
  tab_lease_id text,
  manual integer not null default 0,
  updated_at text not null
);
alter table card_evaluations add column writeback_at text;
alter table opportunity_breakdowns add column writeback_at text;
