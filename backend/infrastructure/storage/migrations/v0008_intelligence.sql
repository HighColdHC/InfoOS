create table interest_profiles (
  profile_version integer primary key check (profile_version > 0),
  markdown text not null,
  sections_json text not null,
  content_hash text not null,
  source text not null,
  created_at text not null
);

create table card_evaluations (
  evaluation_id text primary key,
  card_id text not null references cards(card_id),
  card_version integer not null check (card_version > 0),
  profile_version integer not null references interest_profiles(profile_version),
  prompt_version text not null,
  model text not null,
  provider_id text not null,
  quality integer not null check (quality between 0 and 100),
  relevance integer check (relevance between 0 and 100),
  attribution_kind text check (attribution_kind in ('project','kr','question','domain','exploration','paused')),
  attribution_ref text,
  content_type text not null check (content_type in ('event','method','case','opinion','tool','data')),
  suggested_action text not null check (suggested_action in ('aware','read','distill_candidate','action_candidate')),
  paused_landmark integer not null check (paused_landmark in (0, 1)),
  summary text not null,
  relation_note text not null,
  passed_quality integer not null check (passed_quality in (0, 1)),
  receipt_id text references provider_receipts(receipt_id),
  created_at text not null,
  unique (card_id, card_version, profile_version, prompt_version)
);
create index idx_card_evaluations_window on card_evaluations(passed_quality, created_at desc, relevance desc);
create index idx_card_evaluations_card_latest on card_evaluations(card_id, created_at desc, evaluation_id desc);

create table daily_briefs (
  brief_date text primary key,
  window_start text not null,
  window_end text not null,
  profile_version integer not null references interest_profiles(profile_version),
  items_json text not null,
  generated_at text not null
);
