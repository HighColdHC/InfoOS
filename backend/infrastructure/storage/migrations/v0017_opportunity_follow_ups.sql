create table opportunity_follow_up_events (
  event_id text primary key,
  card_id text not null references cards(card_id),
  state text not null check (state in ('following','dismissed','reset')),
  reason text check (reason is null or length(reason) <= 200),
  actor text not null,
  created_at text not null
);
create index idx_opportunity_follow_up_events_card on opportunity_follow_up_events(card_id, created_at desc);
create index idx_opportunity_follow_up_events_created on opportunity_follow_up_events(created_at desc);
