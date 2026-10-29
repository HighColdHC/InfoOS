alter table raw_items add column content_hash text;
alter table raw_items add column revision integer not null default 0;

create table raw_item_revisions (
  connector_id text not null,
  raw_item_id text not null,
  revision integer not null check (revision > 0),
  content_hash text not null,
  title text not null default '',
  raw_text text,
  observed_at text not null,
  primary key (connector_id, raw_item_id, revision),
  unique (connector_id, raw_item_id, content_hash),
  foreign key (connector_id, raw_item_id) references raw_items(connector_id, raw_item_id)
);
