"""Card-level brief and live update projections."""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict, cast

from backend.domain.timestamps import normalize_timestamp
from backend.domain.intelligence.rubric import EXPLORATION_CONTENT_TYPES, EXPLORATION_QUALITY_THRESHOLD
from backend.infrastructure.storage.card_entry import (
    CARD_FIELDS, ENTRY_JOIN, ENTRY_KEY, CardEntryRow, entry_window, project_display_titles,
)
from backend.infrastructure.storage.database import connect_readonly, default_database_path


class EvaluatedCardRow(CardEntryRow):
    evaluation_id: str
    created_at: str
    quality: int
    relevance: int | None
    passed_quality: int
    attribution_kind: str | None
    attribution_ref: str | None
    content_type: str
    suggested_action: str
    paused_landmark: int
    summary: str
    relation_note: str


class BriefItem(TypedDict):
    number: int
    group: str
    card_id: str
    title: str
    platform: str
    author: str | None
    published_at: str | None
    summary: str
    relation_note: str
    attribution_kind: str | None
    attribution_ref: str | None
    content_type: str
    suggested_action: str
    suggested_action_label: str
    quality: int
    relevance: int | None
    deep_link: str
    source_url: str


class BriefUpdateItem(BriefItem):
    is_digest: bool | None


class BriefUpdates(TypedDict):
    since: str
    related: list[BriefUpdateItem]
    exploration: list[BriefUpdateItem]


RELATED = {"project", "kr", "question", "domain"}
EXPLORATION_FILTER = (
    "e.passed_quality = 1 and e.attribution_kind = 'exploration' "
    f"and e.quality >= {EXPLORATION_QUALITY_THRESHOLD} and e.content_type in ("
    + ", ".join(f"'{kind}'" for kind in EXPLORATION_CONTENT_TYPES) + ")"
)


def _evaluated(base_dir: Path | str, start: str, updates_limit: int) -> list[EvaluatedCardRow]:
    clauses, params = entry_window(start, None)
    clauses.extend([f'{ENTRY_KEY} > ?',
                    "((e.passed_quality = 1 and e.attribution_kind in ('project','kr','question','domain')) "
                    f"or ({EXPLORATION_FILTER}))"])
    params.append(params[0])
    params.append(updates_limit)
    tail = f' order by {ENTRY_KEY} desc, c.card_id desc limit ?'
    query = f"""select {CARD_FIELDS}, {ENTRY_KEY} as entered_at,
            e.evaluation_id, e.created_at, e.quality, e.relevance, e.passed_quality,
            e.attribution_kind, e.attribution_ref, e.content_type, e.suggested_action,
            e.paused_landmark, e.summary, e.relation_note
            from {ENTRY_JOIN} join card_evaluations e on e.evaluation_id = (
                select latest.evaluation_id from card_evaluations latest
                where latest.card_id = c.card_id
                order by latest.created_at desc, latest.evaluation_id desc limit 1)
            where """ + " and ".join(clauses) + tail
    with connect_readonly(default_database_path(base_dir)) as conn:
        rows = conn.execute(query, params).fetchall()
    return [cast(EvaluatedCardRow, dict(row)) for row in rows]


def _item(row: EvaluatedCardRow, group: str, number: int) -> BriefItem:
    card_id = row["card_id"]
    return {
        "number": number, "group": group, "card_id": card_id, "title": row["title"],
        "platform": row["source_platform"], "author": row["source_author"],
        "published_at": row["published_at"], "summary": row["summary"],
        "relation_note": row["relation_note"], "attribution_kind": row["attribution_kind"],
        "attribution_ref": row["attribution_ref"], "content_type": row["content_type"],
        "suggested_action": row["suggested_action"], "suggested_action_label": "AI 建议",
        "quality": row["quality"], "relevance": row["relevance"],
        "deep_link": f"/r/card/{card_id}", "source_url": row["source_url"],
    }


def updates_since(base_dir: Path | str, since_iso: str, limit: int = 20) -> BriefUpdates:
    since = normalize_timestamp(since_iso)
    if since is None or not 1 <= limit <= 100:
        raise ValueError("invalid since or limit")
    rows = _evaluated(base_dir, since, updates_limit=limit)
    project_display_titles(base_dir, rows)
    related = sorted((r for r in rows if r["attribution_kind"] in RELATED),
                     key=lambda r: (r["entered_at"], r["card_id"]), reverse=True)[:limit]
    exploration = sorted((r for r in rows if r["attribution_kind"] == "exploration"),
                         key=lambda r: (r["entered_at"], r["card_id"]), reverse=True)[:limit]
    return {"since": since, "related": [{**_item(r, "related", i + 1), "is_digest": r["is_digest"]} for i, r in enumerate(related)],
            "exploration": [{**_item(r, "exploration", i + 1), "is_digest": r["is_digest"]} for i, r in enumerate(exploration)]}
