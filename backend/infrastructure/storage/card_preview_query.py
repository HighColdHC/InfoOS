"""SQL filtering and pagination for the readable card catalog."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.storage.cards import CardRepository, _CARD_FIELDS
from backend.infrastructure.storage.cards_types import CardRow
from backend.infrastructure.storage.card_preview_query_types import (
    PreviewBlockRow, PreviewAssetRow, PreviewSourceRow, PreviewEvaluationRow,
)


_LATEST_EVALUATION_JOIN = """join card_evaluations e on e.evaluation_id = (
    select latest.evaluation_id from card_evaluations latest
    where latest.card_id = c.card_id
    order by latest.created_at desc, latest.evaluation_id desc limit 1)"""


def _term_clause(term: str, *, card_alias: str = "c") -> tuple[str, str]:
    if len(term) >= 3:
        phrase = '"' + term.replace('"', '""') + '"'
        return f"""{card_alias}.card_id in (
            select card_id from cards where rowid in
              (select rowid from card_text_fts where card_text_fts match ?)
            union
            select b.card_id from card_content_blocks b where b.rowid in
              (select rowid from card_block_text_fts where card_block_text_fts match ?))""", phrase
    value = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    fields = ("coalesce(c.title,'') || char(10) || coalesce(c.source_author,'') || char(10)"
              " || coalesce(c.content,'') || char(10) || coalesce(c.source_url,'')")
    return f"""(
          ({fields}) like ? escape '\\'
          or exists (select 1 from card_content_blocks b where b.card_id = c.card_id
             and b.block_kind not in ('enrichment_gap','llm_context','media_download_gap','image','video')
             and not (c.source_platform in ('xiaohongshu','douyin','scys','zsxq')
               and b.block_kind = 'linked_article' and b.provenance = 'Generic article fetch via async enrichment')
             and b.body like ? escape '\\')
        )""", value


def _published_key(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("published bounds must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")[:26]


def read_preview_rows(
    base_dir: Path, *, query: str | None, platform: str | None,
    completeness: str | None, status: str, sort: str, limit: int, offset: int,
    published_since: datetime | None = None, published_until: datetime | None = None,
    recommended: bool = False,
    source_id: str | None = None,
    evaluation: str | None = None,
    digest: bool | None = None,
) -> tuple[list[CardRow], int]:
    evaluation = "passed" if recommended else evaluation
    if evaluation == "passed" and status == "processing":
        return [], 0
    terms = str(query or "").split()
    has_fts = any(len(term) >= 3 for term in terms)
    indexed_order = sort in {"published_desc", "relevance_desc"} and (has_fts or bool(evaluation))
    # Prefer primary-key lookups from the FTS union over scanning the status index.
    clauses = ["+c.status = ?" if has_fts else "c.status = ?"]
    params: list[Any] = [status]
    if status == "active":
        clauses += ["c.version >= 1", "c.content_hash is not null", "length(trim(coalesce(c.content_hash, ''))) > 0"]
    for column, value in (("source_platform", platform), ("completeness_status", completeness)):
        if value:
            clauses.append(f"c.{column} = ?")
            params.append(value)
    if source_id == "__none__":
        clauses.append("not exists (select 1 from sources s where s.source_id=c.source_id)")
    elif source_id:
        clauses.append("c.source_id = ?")
        params.append(source_id)
    if digest is not None:
        clauses.append("c.source_platform = 'zsxq'")
        clauses.append("json_type(c.raw_payload, '$.raw_payload.is_digest') = ? or "
                       "(json_type(c.raw_payload, '$.raw_payload') is null and "
                       "json_type(c.raw_payload, '$.is_digest') = ?)")
        clauses[-1] = "(" + clauses[-1] + ")"
        params.extend(["true" if digest else "false"] * 2)
    if published_since is not None or published_until is not None:
        # published_ts follows raw_payload in the table record. Read bounds
        # from its covering index, including for count and updated_desc.
        bounds = ["status = ?"]
        params.append(status)
        for bound, operator in ((published_since, ">="), (published_until, "<=")):
            if bound is not None:
                bounds.append(f"published_ts {operator} ?")
                params.append(_published_key(bound))
        clauses.append("c.rowid in (select rowid from cards indexed by idx_cards_status_published "
                       f"where {' and '.join(bounds)})")
    if evaluation == "passed":
        clauses.append("e.passed_quality = 1")
    elif evaluation == "unevaluated":
        clauses.append("e.evaluation_id is null")
    page_clauses = clauses.copy()
    if indexed_order:
        page_clauses[0] = "k.status = ?"
    # Every whitespace-separated term must appear somewhere in the card; a
    # multi-word query is rarely a literal phrase, least of all in Chinese.
    for term in terms:
        clause, value = _term_clause(term)
        clauses.append(clause)
        page_clauses.append(_term_clause(term, card_alias="k" if indexed_order else "c")[0])
        params += [value, value]
    where, page_where = " and ".join(clauses), " and ".join(page_clauses)
    columns = ", ".join(f"c.{column.strip()}" for column in _CARD_FIELDS.split(","))
    repo = CardRepository(base_dir)
    order_alias = "k" if indexed_order else "c"
    ordering = (
        f"{order_alias}.sort_ts desc, {order_alias}.card_id desc"
        if sort in {"published_desc", "relevance_desc"} else "c.updated_at desc, c.card_id desc"
    )
    if sort == "relevance_desc":
        ordering = "e.relevance desc, e.quality desc, " + ordering
    if evaluation == "evaluated":
        ordering = "e.passed_quality desc, " + ordering
    join = ""
    if evaluation:
        join = ("left " if evaluation == "unevaluated" else "") + _LATEST_EVALUATION_JOIN
    page_from = "cards c"
    if indexed_order:
        # Keep the narrow index outermost: check FTS membership before looking
        # up leading filter columns by rowid. Both ordering keys stay covered;
        # reading c.sort_ts would traverse each hit's raw_payload overflow pages.
        page_from = ("cards k indexed by idx_cards_status_sort "
                     "cross join cards c on c.rowid = k.rowid")
    page_join = join
    if sort == "relevance_desc" and evaluation == "passed":
        # Walk scores first; LIMIT can stop after sorting only the tied scores.
        page_from = ("card_evaluations e indexed by idx_card_evaluations_passed_relevance "
                     "join cards c on c.card_id = e.card_id")
        page_join = ""
        page_where = where + """ and e.evaluation_id = (
            select latest.evaluation_id from card_evaluations latest
            where latest.card_id = c.card_id
            order by latest.created_at desc, latest.evaluation_id desc limit 1)"""
        ordering = "e.relevance desc, e.quality desc, c.sort_ts desc, c.card_id desc"
    with repo.connect_existing() as conn:
        # Both statements share a read snapshot, including concurrent finalize writes.
        conn.execute("begin")
        total = conn.execute(f"select count(*) from cards c {join} where {where}", params).fetchone()[0]
        page_ids = [row[0] for row in conn.execute(
            f"select c.card_id from {page_from} {page_join} where {page_where} order by {ordering} limit ? offset ?",
            [*params, limit, offset])]
        by_id = {}
        if page_ids:
            placeholders = ",".join("?" for _ in page_ids)
            # first_seen_at is displayed, so only the bounded page reads raw_items.
            by_id = {row["card_id"]: row for row in conn.execute(
                f"""select {columns}, r.first_seen_at first_seen_at from cards c
                    left join raw_items r on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id
                    where c.card_id in ({placeholders})""", page_ids)}
    rows = [by_id[card_id] for card_id in page_ids]
    cards = [repo._decode(dict(row)) for row in rows]
    for card in cards:
        card["published_at"] = normalize_timestamp(card["published_at"])
    return cards, total


def read_processing_total(base_dir: Path) -> int:
    """Match the unfiltered status=processing list total used by the feed badge."""
    with CardRepository(base_dir).connect_existing() as conn:
        return conn.execute("select count(*) from cards where status = 'processing'").fetchone()[0]


def read_preview_page_content(base_dir: Path, card_ids: list[str], source_ids: list[str]) -> tuple[list[PreviewBlockRow], list[PreviewAssetRow], list[PreviewSourceRow], list[PreviewEvaluationRow]]:
    """Read the page's blocks, assets, source names, and latest evaluations in batches."""
    if not card_ids:
        return [], [], [], []
    placeholders = ",".join("?" for _ in card_ids)
    repo = CardRepository(base_dir)
    with repo.connect_existing() as conn:
        evaluations = [cast(PreviewEvaluationRow, dict(row)) for row in conn.execute(
            f"""select e.card_id, e.quality, e.relevance, e.passed_quality,
                       e.attribution_kind, e.attribution_ref, e.content_type,
                       e.suggested_action, e.summary, e.relation_note, e.created_at as evaluated_at,
                       e.prompt_version, e.profile_version, e.model
                from cards c {_LATEST_EVALUATION_JOIN}
                where c.card_id in ({placeholders})""", card_ids,
        )]
        blocks = [cast(PreviewBlockRow, dict(row)) for row in conn.execute(
            f"""select card_id, position, created_at, block_id, layer, block_kind,
                       substr(body, 1, 1200) as body, raw_payload, provenance
                from card_content_blocks where card_id in ({placeholders})
                  and layer in ('original', 'intelligence')
                  and block_kind not in ('transcript', 'video_transcript', 'translation',
                    'comment', 'platform_comments', 'image', 'video', 'enrichment_gap',
                    'llm_context', 'media_download_gap')
                order by card_id, position, created_at, block_id""", card_ids,
        )]
        assets = [cast(PreviewAssetRow, dict(row)) for row in conn.execute(
            f"""select asset_id, card_id, asset_kind, mime_type, status, cleanup_state, local_path, created_at
                from card_assets where card_id in ({placeholders})
                order by card_id, created_at, asset_id""", card_ids,
        )]
        sources = []
        if source_ids:
            source_placeholders = ",".join("?" for _ in source_ids)
            sources = [cast(PreviewSourceRow, dict(row)) for row in conn.execute(
                f"select source_id, display_name from sources where source_id in ({source_placeholders})",
                source_ids,
            )]
    return blocks, assets, sources, evaluations
