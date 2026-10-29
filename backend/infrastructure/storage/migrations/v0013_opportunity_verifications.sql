create table opportunity_verifications (
  verification_id text primary key,
  card_id text not null references cards(card_id),
  recorder text not null,
  contact_channel text not null check (contact_channel in ('wechat','phone','platform_dm','in_person','other')),
  contacted_on text,
  offer text not null,
  price text,
  delivery text,
  funnel text,
  credibility text not null check (credibility in ('credible','doubtful','scam_suspected','unclear')),
  doubts text,
  replicability integer check (replicability between 0 and 100),
  conclusion text not null check (conclusion in ('keep_observing','drop','to_action')),
  note text,
  supersedes text references opportunity_verifications(verification_id),
  token_id text not null,
  schema_version text not null,
  created_at text not null
);
create index idx_opportunity_verifications_card on opportunity_verifications(card_id, created_at desc);
create index idx_opportunity_verifications_conclusion on opportunity_verifications(conclusion, created_at desc);
create index idx_opportunity_verifications_created on opportunity_verifications(created_at desc);
