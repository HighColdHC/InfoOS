create table opportunity_breakdowns (
  breakdown_id text primary key,
  card_id text not null references cards(card_id),
  card_version integer not null check (card_version >= 0),
  lens_version text not null,
  rules_version text not null,
  evaluator text not null,
  token_id text not null,
  monetization_model text not null check (monetization_model in
    ('course','community','ecommerce','service','consulting','traffic_ads','agency_franchise','tool_product','other','unclear')),
  offer text not null,
  price_signal text,
  funnel text not null,
  target_customer text,
  evidence text not null,
  replicability integer not null check (replicability between 0 and 100),
  credibility integer not null check (credibility between 0 and 100),
  risk_flags text not null,
  next_step text not null check (next_step in ('verify','observe','skip')),
  verify_questions text not null,
  breakdown_note text not null,
  created_at text not null,
  unique (card_id, card_version, lens_version)
);
create index idx_opportunity_breakdowns_card on opportunity_breakdowns(card_id, created_at desc);
create index idx_opportunity_breakdowns_next_step on opportunity_breakdowns(next_step, created_at desc);
create index idx_opportunity_breakdowns_created on opportunity_breakdowns(created_at desc, breakdown_id desc);
-- Candidate rules apply to all card states, so the active-only v9 index is insufficient.
create index idx_cards_opportunity_raw on cards(raw_connector_id, raw_item_id, card_id);
