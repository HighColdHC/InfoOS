"""SQLite-backed context projections used by the task manager."""
from __future__ import annotations

from backend.domain.card_titles import display_title
from backend.domain.insights import is_valid_insight_row

from pathlib import Path
from typing import TypeVar, TypedDict, cast

from backend.domain.json_types import JsonObject

from backend.domain.tasks import Task
from backend.infrastructure.storage.base import loads
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path


class CardSourceRow(TypedDict):
    card_id: str
    source_id: str | None


class ContextCardRow(TypedDict):
    card_id: str
    source_platform: str
    title: str
    content: str
    summary: str
    source_url: str


class CompactAsset(TypedDict):
    asset_id: str
    asset_kind: str
    status: str
    source_url: str
    remote_url: str | None
    local_path: str | None
    mime_type: str | None


class ContextAssetRow(CompactAsset):
    card_id: str


class CompactBlock(TypedDict):
    block_id: str
    block_kind: str
    status: str
    body: str
    segments: list[object]
    source_url: str | None
    provenance: str
    raw_payload: JsonObject


class ContextBlockRow(CompactBlock):
    card_id: str


class StoredContextBlockRow(TypedDict):
    card_id: str
    block_id: str
    block_kind: str
    status: str
    body: str
    segments: str
    source_url: str | None
    provenance: str
    raw_payload: str


class CompactInsight(TypedDict):
    main_point: str
    why_it_matters: str
    status: str
    last_error: str | None
    model: str | None
    generated_at: str


class ContextInsightRow(CompactInsight):
    card_id: str


class CardContext(TypedDict):
    card_id: str
    title: str
    content: str
    summary: str
    source_url: str
    images: list[CompactAsset]
    videos: list[CompactAsset]
    video_transcripts: list[CompactBlock]
    content_blocks: list[CompactBlock]
    ai_insight: CompactInsight | None


_Row = TypeVar("_Row")


def task_card_id(task: Task) -> str | None:
    if task.target_kind == "card" and task.target_id:
        return task.target_id
    card_id = task.artifacts_summary.get("card_id")
    return str(card_id) if card_id else None


def source_by_card_id(base_dir: Path, tasks: list[Task]) -> dict[str, str]:
    card_ids = sorted({card_id for task in tasks if (card_id := task_card_id(task))})
    return {
        card["card_id"]: card["source_id"]
        for card in _context_rows(base_dir, card_ids, "cards", "card_id, source_id", CardSourceRow)
        if card["source_id"]
    }


def card_context_by_id(base_dir: Path, tasks: list[Task]) -> dict[str, CardContext]:
    card_ids = sorted({card_id for task in tasks if (card_id := task_card_id(task))})
    if not card_ids:
        return {}
    cards = _context_rows(
        base_dir, card_ids, "cards",
        "card_id, source_platform, title, content, summary, source_url", ContextCardRow,
    )
    blocks_by_card: dict[str, list[ContextBlockRow]] = {}
    for stored_block in _context_rows(
        base_dir, card_ids, "card_content_blocks",
        "card_id, block_id, block_kind, status, body, segments, source_url, provenance, raw_payload", StoredContextBlockRow,
        order_by="position, created_at, block_id",
    ):
        block = _parse_block(stored_block)
        blocks_by_card.setdefault(block["card_id"], []).append(block)
    assets_by_card: dict[str, list[ContextAssetRow]] = {}
    for asset in _context_rows(
        base_dir, card_ids, "card_assets",
        "card_id, asset_id, asset_kind, status, source_url, remote_url, local_path, mime_type", ContextAssetRow,
        order_by="created_at, asset_id",
    ):
        assets_by_card.setdefault(asset["card_id"], []).append(asset)
    insights = _current_insights(base_dir, card_ids)
    contexts: dict[str, CardContext] = {}
    for card in cards:
        card_id = card["card_id"]
        blocks = blocks_by_card.get(card_id, [])
        assets = assets_by_card.get(card_id, [])
        insight = insights.get(card_id)
        contexts[card_id] = {
            "card_id": card_id,
            "title": display_title(card["source_platform"], card["title"], card["content"]),
            "content": _truncate_text(card["content"]),
            "summary": _truncate_text(card["summary"]),
            "source_url": card["source_url"],
            "images": [_compact_asset(asset) for asset in assets if asset["asset_kind"] == "image"],
            "videos": [_compact_asset(asset) for asset in assets if asset["asset_kind"] == "video"],
            "video_transcripts": [_compact_block(block) for block in blocks if _is_video_transcript_block(block)],
            "content_blocks": [_compact_block(block) for block in blocks],
            "ai_insight": _compact_insight(insight) if insight else None,
        }
    return contexts


def _context_rows(
    base_dir: Path, card_ids: list[str], table: str, columns: str, row_type: type[_Row], *, order_by: str = "card_id",
) -> list[_Row]:
    """Internal queries use fixed table/column names and bounded card identities."""
    if not card_ids:
        return []
    result: list[_Row] = []
    with connect_existing(resolve_sqlite_path(base_dir)) as conn:
        for start in range(0, len(card_ids), 400):
            chunk = card_ids[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"select {columns} from {table} where card_id in ({placeholders}) order by {order_by}", chunk,
            ).fetchall()
            result.extend(cast(_Row, dict(row)) for row in rows)
    return result


def _current_insights(base_dir: Path, card_ids: list[str]) -> dict[str, ContextInsightRow]:
    """Preserve the legacy context field using each card's latest SUMMARY task."""
    insights: dict[str, ContextInsightRow] = {}
    with connect_existing(resolve_sqlite_path(base_dir)) as conn:
        for start in range(0, len(card_ids), 400):
            chunk = card_ids[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                select v.card_id, v.main_point, v.why_it_matters, v.status,
                       v.last_error, v.model, v.generated_at
                from cards c
                join tasks t on t.rowid = (
                    select latest.rowid from tasks latest
                    where latest.task_type = 'summary' and latest.target_kind = 'card'
                      and latest.target_id = c.card_id
                    order by latest.created_at desc, latest.rowid desc limit 1
                )
                join card_ai_insight_versions v
                  on v.card_id = t.target_id and v.input_version = t.input_version
                where c.card_id in ({placeholders}) and t.status = 'success'
                  and t.result_version = t.input_version
                """, chunk,
            ).fetchall()
            for row in rows:
                insight = cast(ContextInsightRow, dict(row))
                if is_valid_insight_row(insight):
                    insights[insight["card_id"]] = insight
    return insights


def _parse_block(row: StoredContextBlockRow) -> ContextBlockRow:
    segments: object = loads(row["segments"], [])
    raw_payload: object = loads(row["raw_payload"], {})
    return {
        "card_id": row["card_id"], "block_id": row["block_id"],
        "block_kind": row["block_kind"], "status": row["status"], "body": row["body"],
        "source_url": row["source_url"], "provenance": row["provenance"],
        "segments": segments if isinstance(segments, list) else [],
        "raw_payload": raw_payload if isinstance(raw_payload, dict) else {},
    }


def _compact_asset(asset: ContextAssetRow) -> CompactAsset:
    return {
        "asset_id": asset["asset_id"],
        "asset_kind": asset["asset_kind"],
        "status": asset["status"],
        "source_url": asset["source_url"],
        "remote_url": asset["remote_url"],
        "local_path": asset["local_path"],
        "mime_type": asset["mime_type"],
    }


def _compact_block(block: ContextBlockRow) -> CompactBlock:
    return {
        "block_id": block["block_id"], "block_kind": block["block_kind"], "status": block["status"],
        "body": block["body"],
        "segments": block["segments"],
        "source_url": block["source_url"], "provenance": block["provenance"],
        "raw_payload": block["raw_payload"],
    }


def _compact_insight(insight: ContextInsightRow) -> CompactInsight:
    return {
        "main_point": _truncate_text(insight["main_point"]), "why_it_matters": _truncate_text(insight["why_it_matters"]),
        "status": insight["status"], "last_error": insight["last_error"], "model": insight["model"], "generated_at": insight["generated_at"],
    }


def _is_video_transcript_block(block: ContextBlockRow) -> bool:
    values = f"{block['block_kind']} {block['provenance']}".lower()
    return "transcript" in values or "transcribe" in values


def _truncate_text(text: str, *, limit: int = 2000) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
