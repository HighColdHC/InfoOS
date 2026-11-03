"""Legacy candidate SQL fragments retained for the agent watchdog."""
PREFILTER_CANDIDATE = "f.prefilter_candidate=1"
WIND_VANE_CANDIDATE = "(s.platform='zsxq' and (s.source_type='opportunity' or f.wind_vane_tagged=1))"
CASE_CANDIDATE = """exists(select 1 from card_evaluations e where e.evaluation_id=(
    select latest.evaluation_id from card_evaluations latest where latest.card_id=c.card_id
    order by latest.created_at desc, latest.evaluation_id desc limit 1)
    and e.content_type='case' and e.passed_quality=1 and e.relevance>=60)"""
CANDIDATE = f"({PREFILTER_CANDIDATE} or {WIND_VANE_CANDIDATE} or {CASE_CANDIDATE})"
ENTRY_JOIN = """raw_items r indexed by idx_raw_items_first_seen_source
    cross join cards c indexed by idx_cards_opportunity_raw
    on c.raw_connector_id=r.connector_id and c.raw_item_id=r.raw_item_id
    join raw_item_candidate_flags f on f.connector_id=r.connector_id and f.raw_item_id=r.raw_item_id
    left join sources s on s.source_id=r.source_id"""
CURRENT_BREAKDOWN = """exists(select 1 from opportunity_breakdowns b where b.card_id=c.card_id
    and b.breakdown_id=(select n.breakdown_id from opportunity_breakdowns n where n.card_id=c.card_id
        order by n.created_at desc, n.breakdown_id desc limit 1)
    and b.lens_version=? and b.profile_version=? and b.raw_item_revision=r.revision)"""
