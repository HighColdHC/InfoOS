create table provider_receipts (
  receipt_id text primary key,
  logical_key text not null unique,
  service text not null,
  provider_id text not null,
  model text not null,
  purpose text not null,
  subject text not null,
  status text not null check (status in ('pending','received','completed','failed','unknown')),
  attempts integer not null default 0,
  unknown_count integer not null default 0,
  request_summary text not null,
  response text,
  usage text,
  error text,
  created_at text not null,
  updated_at text not null,
  received_at text,
  completed_at text
);
create table provider_receipt_attempts (
  attempt_id text primary key,
  receipt_id text not null references provider_receipts(receipt_id),
  attempt integer not null,
  provider_id text not null,
  model text not null,
  status text not null check (status in ('pending','received','completed','failed','unknown')),
  started_at text not null,
  finished_at text,
  latency_ms integer,
  error text,
  unique (receipt_id, attempt)
);
create index idx_provider_receipt_attempts_budget on provider_receipt_attempts(provider_id, started_at);
create index idx_provider_receipts_status_updated on provider_receipts(status, updated_at);
