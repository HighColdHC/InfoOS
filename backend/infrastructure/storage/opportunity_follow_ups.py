"""Append-only follow-up events; the caller owns write transactions."""
import sqlite3
from typing import cast

from backend.domain.intelligence.opportunity_follow_up import FollowUpFact
from backend.infrastructure.storage.base import InfoOSRepository


class FollowUpEvent(FollowUpFact):
    event_id: str
    card_id: str
    reason: str | None
    actor: str


COLUMNS = "event_id, card_id, state, reason, actor, created_at"


class OpportunityFollowUpRepository(InfoOSRepository):
    @staticmethod
    def append(conn: sqlite3.Connection, event: FollowUpEvent) -> None:
        conn.execute(f"insert into opportunity_follow_up_events ({COLUMNS}) "
                     "values (:event_id, :card_id, :state, :reason, :actor, :created_at)", event)

    def latest(self, card_ids: list[str], *, connection: sqlite3.Connection | None = None) -> dict[str, FollowUpEvent]:
        if connection is None:
            with self.connect_existing() as conn:
                return self.latest(card_ids, connection=conn)
        result = {}
        for offset in range(0, len(card_ids), 400):
            batch = card_ids[offset:offset + 400]
            rows = connection.execute(f"select {COLUMNS} from opportunity_follow_up_events f "
                f"where card_id in ({','.join('?' for _ in batch)}) and event_id=("
                "select n.event_id from opportunity_follow_up_events n where n.card_id=f.card_id "
                "order by n.created_at desc,n.event_id desc limit 1)", batch).fetchall()
            result.update({row["card_id"]: cast(FollowUpEvent, dict(row)) for row in rows})
        return result

    def for_card(self, card_id: str) -> list[FollowUpEvent]:
        with self.connect_existing() as conn:
            rows = conn.execute(f"select {COLUMNS} from opportunity_follow_up_events where card_id=? "
                                "order by created_at,event_id", (card_id,)).fetchall()
        return [cast(FollowUpEvent, dict(row)) for row in rows]
