create table card_asset_thumbnails (
  asset_id text primary key references card_assets(asset_id) on delete cascade,
  status text not null check (status in ('ready', 'failed')),
  local_path text,
  width integer,
  height integer,
  size_bytes integer,
  source_content_hash text,
  error_code text,
  created_at text not null,
  updated_at text not null,
  check ((status = 'ready' and local_path is not null and width is not null and height is not null
          and size_bytes is not null and error_code is null)
      or (status = 'failed' and local_path is null and width is null and height is null
          and size_bytes is null and error_code is not null))
);
