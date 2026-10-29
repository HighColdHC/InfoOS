alter table api_tokens add column webhook_include_backfill integer not null default 0
  check (webhook_include_backfill in (0, 1));
