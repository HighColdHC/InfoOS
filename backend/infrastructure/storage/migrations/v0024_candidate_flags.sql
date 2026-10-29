create table raw_item_candidate_flags (
    connector_id text not null,
    raw_item_id text not null,
    prefilter_candidate integer not null,
    wind_vane_tagged integer not null,
    primary key (connector_id, raw_item_id),
    foreign key (connector_id, raw_item_id) references raw_items(connector_id, raw_item_id) on delete cascade
) without rowid;

create trigger raw_item_candidate_flags_after_insert after insert on raw_items begin
    insert into raw_item_candidate_flags
        (connector_id, raw_item_id, prefilter_candidate, wind_vane_tagged)
    values (new.connector_id, new.raw_item_id,
        case when {prefilter_new} then 1 else 0 end,
        case when {wind_vane_new} then 1 else 0 end)
    on conflict (connector_id, raw_item_id) do update set
        prefilter_candidate=excluded.prefilter_candidate,
        wind_vane_tagged=excluded.wind_vane_tagged;
end;

create trigger raw_item_candidate_flags_after_update after update of raw_payload on raw_items begin
    insert into raw_item_candidate_flags
        (connector_id, raw_item_id, prefilter_candidate, wind_vane_tagged)
    values (new.connector_id, new.raw_item_id,
        case when {prefilter_new} then 1 else 0 end,
        case when {wind_vane_new} then 1 else 0 end)
    on conflict (connector_id, raw_item_id) do update set
        prefilter_candidate=excluded.prefilter_candidate,
        wind_vane_tagged=excluded.wind_vane_tagged;
end;

insert into raw_item_candidate_flags (connector_id, raw_item_id, prefilter_candidate, wind_vane_tagged)
select r.connector_id, r.raw_item_id,
    case when {prefilter_raw} then 1 else 0 end,
    case when {wind_vane_raw} then 1 else 0 end
from raw_items r;
