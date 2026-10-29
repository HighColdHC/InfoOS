create table reader_page_views (
    view_id integer primary key autoincrement,
    page text not null,
    target_id text,
    ref text,
    user_agent text,
    created_at text not null
);
create index idx_reader_page_views_created on reader_page_views(created_at desc);
