alter table card_evaluations add column exclusion text
  check (exclusion is null or exclusion in ('low_quality','ad','entertainment','paused'));
alter table card_evaluations add column extract_kinds text;   -- JSON 数组；rubric_v1 行为 null

create table knowledge_extractions (          -- 一张信息卡的一次拆解；“拆不出”也要记，避免重复进候选
  extraction_id text primary key,
  card_id text not null references cards(card_id),
  card_version integer not null,
  extraction_version text not null,           -- knowledge_v1
  profile_version integer not null references interest_profiles(profile_version),
  channel text not null check (channel in ('deterministic','triage')),
  outcome text not null check (outcome in ('extracted','nothing')),
  nothing_reason text check (nothing_reason is null or length(nothing_reason) <= 200),
  evaluator text not null, token_id text not null, created_at text not null,
  unique (card_id, card_version, extraction_version)
);
create table knowledge_cards (
  knowledge_id text primary key,              -- kc_ 前缀
  kind text not null, subtype text not null, title text not null,
  fields_json text not null,
  attribution_kind text not null check (attribution_kind in ('project','kr','question','domain','none')),
  attribution_ref text, use_note text not null,
  strength integer not null check (strength between 0 and 100),
  origin_published_at text,                   -- 证据卡中最早的发布时间；判定时效与存量
  origin_freshness text not null check (origin_freshness in ('fresh','recent','stock')),  -- 首次创建时冻结
  status text not null default 'candidate' check (status in ('candidate','adopted','ignored','superseded')),
  superseded_by text references knowledge_cards(knowledge_id),
  research_status text check (research_status is null or research_status in ('pending','researching','project','observe','drop')),
  extraction_version text not null, profile_version integer not null,
  evaluator text not null, created_at text not null, updated_at text not null
);
create index idx_knowledge_cards_kind_created on knowledge_cards(kind, created_at desc);
create index idx_knowledge_cards_created on knowledge_cards(created_at desc, knowledge_id desc);
create table knowledge_evidence (
  evidence_id text primary key,
  knowledge_id text not null references knowledge_cards(knowledge_id),
  card_id text not null references cards(card_id),
  extraction_id text not null references knowledge_extractions(extraction_id),
  action text not null check (action in ('new','merge','update','conflict')),
  quotes_json text not null,
  note text check (note is null or length(note) <= 300),   -- update/conflict 必填：变了什么、和哪条旧结论矛盾
  created_at text not null,
  unique (knowledge_id, card_id)
);
create index idx_knowledge_evidence_card on knowledge_evidence(card_id);
create index idx_knowledge_evidence_created on knowledge_evidence(created_at desc);
create table knowledge_status_events (       -- 用户动作只追加；一期只有 research_status
  event_id text primary key,
  knowledge_id text not null references knowledge_cards(knowledge_id),
  field text not null check (field in ('research_status')),
  value text not null, note text check (note is null or length(note) <= 200),
  actor text not null, created_at text not null
);
create index idx_knowledge_status_events_card on knowledge_status_events(knowledge_id, created_at desc);
