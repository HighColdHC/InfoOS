create index if not exists idx_raw_items_first_seen_source
  on raw_items(first_seen_at, source_id);
