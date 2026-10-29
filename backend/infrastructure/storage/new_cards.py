"""Windowed, keyset-paginated cards for external evaluation."""
from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict, cast

from backend.domain.advertising import AdMetadata

from backend.infrastructure.storage.card_entry import ENTRY_JOIN, ENTRY_KEY, entry_window
from backend.infrastructure.storage.database import connect_readonly, default_database_path


class NewCardRow(TypedDict):
    card_id: str
    title: str
    platform: str
    author: str | None
    published_at: str | None
    raw_payload: NotRequired[object]
    freshness: NotRequired[str]
    first_seen_at: str
    has_evaluation: int | bool
    is_digest: NotRequired[bool | None]
    excerpt: NotRequired[str]
    deep_link: NotRequired[NewCardDeepLink]
    ad: NotRequired[AdMetadata]


class NewCardDeepLink(TypedDict):
    url: str
    relative_url: bool


def read_new_cards(base_dir: Path, *, since: str, until: str | None, after: tuple[str, str] | None,
                   limit: int, only_unevaluated: bool, platform: str | None, rubric_version: str) -> list[NewCardRow]:
    clauses, params = entry_window(since, until, after, platform)
    if only_unevaluated:
        clauses.append("not exists (select 1 from card_evaluations e where e.card_id = c.card_id "
                       "and e.profile_version = (select max(profile_version) from interest_profiles) and e.prompt_version = ?)")
        params.append(rubric_version)
    with connect_readonly(default_database_path(base_dir)) as conn:
        rows = conn.execute(f"""select c.card_id, c.title, c.source_platform as platform, c.source_author as author,
            c.published_at, c.raw_payload, {ENTRY_KEY} as first_seen_at,
            exists(select 1 from card_evaluations e where e.card_id = c.card_id) as has_evaluation
            from {ENTRY_JOIN} where """ + " and ".join(clauses) + f" order by {ENTRY_KEY}, c.card_id limit ?",
            (*params, limit + 1)).fetchall()
    return [cast(NewCardRow, dict(row)) for row in rows]
