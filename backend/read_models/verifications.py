"""Read verification history with filter-bound cursors and display titles."""
from pathlib import Path

from backend.domain.intelligence.verification import CONCLUSIONS
from backend.infrastructure.storage.opportunity_verifications import (
    OpportunityVerificationListRow, OpportunityVerificationRepository,
)
from backend.read_models.opportunities import OpportunityPage, _context, _cursor, _limit, _page, _stamp


def list_verifications(base_dir: Path, card_id: str | None = None, since: str | None = None,
                       conclusion: str | None = None, limit: int = 20,
                       cursor: str | None = None) -> OpportunityPage[OpportunityVerificationListRow]:
    _limit(limit)
    if card_id is not None and (not isinstance(card_id, str) or not card_id.strip()):
        raise ValueError("invalid_card_id")
    if conclusion is not None and (not isinstance(conclusion, str) or conclusion not in CONCLUSIONS):
        raise ValueError("invalid_conclusion")
    start = _stamp(since) if since is not None else None
    context = _context(["verifications", card_id, start, conclusion])
    rows = OpportunityVerificationRepository(base_dir).page(
        card_id=card_id, since=start, conclusion=conclusion, after=_cursor(cursor, context), limit=limit)
    return _page(base_dir, rows, limit, context, "created_at", "verification_id")
