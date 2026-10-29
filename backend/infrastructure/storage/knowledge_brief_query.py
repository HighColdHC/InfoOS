"""Windowed knowledge cards, with a frozen earliest origin link."""
from pathlib import Path
from typing import Any

from backend.domain.timestamps import parse_utc
from backend.infrastructure.storage.knowledge_queries import COLUMNS, KnowledgeQueryRepository, decode


def brief_knowledge_rows(base_dir: Path | str, start: str, end: str) -> list[dict[str, Any]]:
    # Knowledge writes always use UTC microseconds; normalize both boundaries.
    start, end = (parse_utc(stamp).isoformat(timespec="microseconds").replace("+00:00", "Z") for stamp in (start, end))
    with KnowledgeQueryRepository(base_dir).connect_existing() as conn:
        rows = conn.execute(f"""select {','.join('k.' + field.strip() for field in COLUMNS.split(','))},
            (k.created_at>=? and k.created_at<?) as is_new,
            exists(select 1 from knowledge_evidence u where u.knowledge_id=k.knowledge_id
                and u.action='update' and u.created_at>=? and u.created_at<?) as is_update,
            (select e.card_id from knowledge_evidence e where e.knowledge_id=k.knowledge_id
                order by e.created_at,e.evidence_id limit 1) as origin_card_id
            from knowledge_cards k where (k.created_at>=? and k.created_at<?) or exists (
                select 1 from knowledge_evidence e where e.knowledge_id=k.knowledge_id and e.action='update'
                    and e.created_at>=? and e.created_at<?)""", (start, end, start, end, start, end, start, end)).fetchall()
    return [decode(row) for row in rows]
