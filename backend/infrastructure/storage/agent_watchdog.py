"""Watchdog facts from accepted evaluation rows and the shared candidate predicate."""
from backend.domain.intelligence.opportunity import OPPORTUNITY_LENS_VERSION
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.opportunity_candidates import CANDIDATE, ENTRY_JOIN, CURRENT_BREAKDOWN


class AgentWatchdogRepository(InfoOSRepository):
    def facts(self, *, older_than: str, evaluate_since: str, opportunities_since: str) -> list[dict]:
        with self.connect_existing() as conn:
            evaluation = conn.execute("""select count(*) as pending,min(r.first_seen_at) as oldest
                from cards c join raw_items r on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id
                where c.status='active' and r.first_seen_at>=? and r.first_seen_at<? and not exists
                (select 1 from card_evaluations e where e.card_id=c.card_id and e.provider_id like 'agent:%')""",
                (evaluate_since, older_than)).fetchone()
            last_eval = conn.execute("""select max(coalesce(writeback_at,created_at)) from card_evaluations
                where provider_id like 'agent:%'""").fetchone()[0]
            profile = conn.execute('select max(profile_version) from interest_profiles').fetchone()[0]
            opportunity = conn.execute(f"""select count(*) as pending,min(r.first_seen_at) as oldest from {ENTRY_JOIN}
                where r.first_seen_at>=? and r.first_seen_at<? and {CANDIDATE} and not {CURRENT_BREAKDOWN}""",
                (opportunities_since, older_than, OPPORTUNITY_LENS_VERSION, profile)).fetchone()
            last_breakdown = conn.execute('select max(coalesce(writeback_at,created_at)) from opportunity_breakdowns').fetchone()[0]
        return [dict(evaluation, job='evaluate', last_writeback=last_eval),
                dict(opportunity, job='opportunities', last_writeback=last_breakdown)]
