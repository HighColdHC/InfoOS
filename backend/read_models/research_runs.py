"""Read-only projections for research batches and their information cards."""
from __future__ import annotations

from backend.domain.card_titles import display_title

import base64
import json
from pathlib import Path
import sqlite3
from collections.abc import Iterable, Sized
from typing import NotRequired, TypedDict, cast

from backend.domain.json_types import JsonObject

from backend.domain.sources.registry import platform_definition
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.research_runs import research_batch_rows
from backend.infrastructure.storage.task_batch_queries import BatchRow, get_task_batch


class ResearchSummary(TypedDict):
    research_run_id: str
    name: str
    status: str
    keywords: list[object]
    created_at: str
    updated_at: str
    platform_count: int
    completed_platform_count: int
    failed_platform_count: int


class PlatformRun(TypedDict):
    platform: str
    label: str
    status: str
    source_run_id: str
    error: str | None
    limitation: object
    accepted_items: object
    _coverage: NotRequired[list[JsonObject]]


class ResearchCard(TypedDict):
    card_id: str
    title: str
    platform: str
    published_at: str | None
    source_url: str


class ResearchCardPage(TypedDict):
    items: list[ResearchCard]
    next_cursor: str | None


class ResearchRunDetail(TypedDict):
    ok: bool
    research_run: ResearchSummary
    platform_runs: list[PlatformRun]
    coverage: list[JsonObject]
    item_page: ResearchCardPage


class ParsedCoverage(TypedDict):
    limitation: object
    accepted_items: object
    rows: list[JsonObject]


class ResearchRequest(TypedDict):
    name: str
    keywords: list[object]
    platform_count: int


class PlatformTaskRow(TypedDict):
    task_id: str
    platform: str
    status: str
    error_reason: str | None
    artifacts_summary: str
    task_type: str
    target_kind: str
    target_id: str
    depends_on: str
    attempt: int
    max_attempts: int
    next_run_at: str | None
    claimed_by: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    started_at: str | None
    finished_at: str | None
    input_version: str
    result_version: str | None
    last_failure_class: str | None
    completion_token: str | None
    queue_name: str | None
    priority: int | None
    deadline_at: str | None
    created_at: str
    updated_at: str


class ResearchCardRow(TypedDict):
    card_id: str
    title: str
    source_platform: str
    published_at: str | None
    source_url: str


class CardBodyRow(TypedDict):
    card_id: str
    content: str


class BatchCardTaskRow(TypedDict):
    target_kind: str
    target_id: str
    artifacts_summary: str


class PlatformStatusRow(TypedDict):
    status: str


def read_research_runs(
    base_dir: Path | str,
    *,
    limit: int = 20,
) -> list[ResearchSummary]:
    db_path = resolve_sqlite_path(base_dir)
    if not db_path.is_file():
        return []
    with connect_existing(db_path) as conn:
        rows = research_batch_rows(conn, limit=limit)
    return [_research_summary(Path(base_dir), row) for row in rows]


def read_research_run(
    base_dir: Path | str,
    research_run_id: str,
    *,
    item_limit: int = 25,
    cursor: str | None = None,
) -> ResearchRunDetail | None:
    base_path = Path(base_dir)
    db_path = resolve_sqlite_path(base_path)
    if not db_path.is_file():
        return None
    with connect_existing(db_path) as conn:
        row = conn.execute(
            """
            select * from task_batches
            where batch_id = ? and batch_type = 'research'
            """,
            (research_run_id,),
        ).fetchone()
        if row is None:
            return None
        root_rows = conn.execute(
            """
            select t.*, s.platform
            from task_batch_items bi
            join tasks t on t.task_id = bi.task_id
            join sources s on s.source_id = t.target_id
            where bi.batch_id = ? and t.task_type = 'fetch'
              and t.target_kind = 'source'
              and s.source_scope = 'research'
            order by s.platform
            """,
            (research_run_id,),
        ).fetchall()
        platform_runs = [_platform_run(cast(PlatformTaskRow, dict(item))) for item in root_rows]
        coverage = _coverage_rows(platform_runs)
        card_ids = _batch_card_ids(conn, research_run_id)
        page = _card_page(
            conn,
            card_ids=card_ids,
            limit=item_limit,
            cursor=cursor,
        )
    return {
        "ok": True,
        "research_run": _research_summary(base_path, cast(BatchRow, dict(row))),
        "platform_runs": platform_runs,
        "coverage": coverage,
        "item_page": page,
    }


def _research_summary(base_dir: Path, row: BatchRow) -> ResearchSummary:
    request = _parse_request(row["metadata"], row["batch_id"])
    batch = get_task_batch(base_dir, row["batch_id"])
    completed_platform_count, failed_platform_count = _root_platform_counts(
        base_dir,
        row["batch_id"],
    )
    return {
        "research_run_id": row["batch_id"],
        "name": request["name"],
        "status": _public_status(batch["status"]),
        "keywords": request["keywords"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "platform_count": request["platform_count"],
        "completed_platform_count": completed_platform_count,
        "failed_platform_count": failed_platform_count,
    }


def _platform_run(row: PlatformTaskRow) -> PlatformRun:
    platform = row["platform"]
    definition = platform_definition(platform)
    coverage = _parse_coverage(
        row["artifacts_summary"],
        definition.research.limitation if definition and definition.research else None,
    )
    return {
        "platform": platform,
        "label": definition.label if definition else platform,
        "status": _public_status(row["status"]),
        "source_run_id": row["task_id"],
        "error": row["error_reason"],
        "limitation": coverage["limitation"],
        "accepted_items": coverage["accepted_items"],
        "_coverage": coverage["rows"],
    }


def _coverage_rows(platform_runs: list[PlatformRun]) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for item in platform_runs:
        rows.extend(item.pop("_coverage"))
    return rows

def _card_page(
    conn: sqlite3.Connection,
    *,
    card_ids: list[str],
    limit: int,
    cursor: str | None,
) -> ResearchCardPage:
    page_size = max(1, min(int(limit), 100))
    if not card_ids:
        return {"items": [], "next_cursor": None}
    cursor_published, cursor_card_id = decode_research_cursor(cursor)
    placeholders = ", ".join("?" for _ in card_ids)
    where = [
        "card_type = 'information'",
        "status = 'active'",
        "completeness_status in ('complete', 'partial')",
        f"card_id in ({placeholders})",
    ]
    params: list[str | int] = [*card_ids]
    if cursor_published is not None and cursor_card_id is not None:
        where.append(
            "(coalesce(source_timestamp(published_at), '') < ? or "
            "(coalesce(source_timestamp(published_at), '') = ? and card_id < ?))"
        )
        params.extend([cursor_published, cursor_published, cursor_card_id])
    params.append(page_size + 1)
    rows = conn.execute(
        """
        select card_id, title, source_platform, source_timestamp(published_at) published_at, source_url
        from cards
        where """
        + " and ".join(where)
        + """
        order by coalesce(source_timestamp(published_at), '') desc, card_id desc
        limit ?
        """,
        tuple(params),
    ).fetchall()
    has_more = len(rows) > page_size
    selected = [cast(ResearchCardRow, dict(row)) for row in rows[:page_size]]
    bodies = {}
    if selected:
        placeholders = ','.join('?' for _ in selected)
        body_rows = [cast(CardBodyRow, dict(row)) for row in conn.execute(
            f'select card_id, content from cards where card_id in ({placeholders})',
            [row['card_id'] for row in selected])]
        bodies = {row['card_id']: row['content'] for row in body_rows}
    items: list[ResearchCard] = [
        {
            "card_id": row["card_id"],
            "title": display_title(row["source_platform"], row["title"], bodies[row["card_id"]]),
            "platform": row["source_platform"],
            "published_at": row["published_at"],
            "source_url": row["source_url"],
        }
        for row in selected
    ]
    next_cursor = None
    if has_more and selected:
        last = selected[-1]
        next_cursor = _encode_cursor(
            last["published_at"] or "",
            last["card_id"],
        )
    return {"items": items, "next_cursor": next_cursor}


def _batch_card_ids(conn: sqlite3.Connection, batch_id: str) -> list[str]:
    """Project durable batch-to-card provenance from inherited task membership."""
    rows = conn.execute(
        """
        select t.target_kind, t.target_id, t.artifacts_summary
        from task_batch_items bi
        join tasks t on t.task_id = bi.task_id
        where bi.batch_id = ?
        order by bi.rowid
        """,
        (batch_id,),
    ).fetchall()
    card_ids: list[str] = []
    for raw_row in rows:
        row = cast(BatchCardTaskRow, dict(raw_row))
        artifacts = _json_object(row["artifacts_summary"])
        candidates = [artifacts.get("card_id")]
        if row["target_kind"] == "card":
            candidates.append(row["target_id"])
        for value in candidates:
            card_id = str(value or "").strip()
            if card_id and card_id not in card_ids:
                card_ids.append(card_id)
    return card_ids


def _root_platform_counts(base_dir: Path, batch_id: str) -> tuple[int, int]:
    db_path = resolve_sqlite_path(base_dir)
    with connect_existing(db_path) as conn:
        rows = conn.execute(
            """
            select t.status
            from task_batch_items bi
            join tasks t on t.task_id = bi.task_id
            join sources s on s.source_id = t.target_id
            where bi.batch_id = ? and t.task_type = 'fetch'
              and t.target_kind = 'source'
              and s.source_scope = 'research'
            """,
            (batch_id,),
        ).fetchall()
    statuses = [cast(PlatformStatusRow, dict(row)) for row in rows]
    completed = sum(row["status"] in {"success", "skipped"} for row in statuses)
    failed = sum(row["status"] in {"failed", "blocked"} for row in statuses)
    return completed, failed


def _encode_cursor(published_at: str, card_id: str) -> str:
    encoded = json.dumps(
        {"published_at": published_at, "card_id": card_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def decode_research_cursor(value: str | None) -> tuple[str | None, str | None]:
    """Validate a research page cursor, including before any results exist."""
    if value is None or value == "":
        return None, None
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
        published_at = payload["published_at"]
        card_id = payload["card_id"]
        if not isinstance(published_at, str) or not isinstance(card_id, str) or not card_id:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_cursor") from exc
    return published_at, card_id


def _json_object(value: object) -> JsonObject:
    try:
        loaded = json.loads(cast(str, value) or "{}") if not isinstance(value, dict) else value
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_request(raw: object, batch_id: str) -> ResearchRequest:
    metadata = _json_object(raw)
    request_value = metadata.get("request")
    request = request_value if isinstance(request_value, dict) else {}
    return {
        "name": str(request.get("name") or batch_id),
        "keywords": list(cast(Iterable[object], request.get("keywords") or [])),
        "platform_count": len(cast(Sized, request.get("platforms") or [])),
    }


def _parse_coverage(raw: object, default_limitation: object) -> ParsedCoverage:
    artifacts = _json_object(raw)
    coverage = artifacts.get("research_coverage")
    if not isinstance(coverage, dict):
        checkpoint = artifacts.get("acquisition_checkpoint")
        coverage = checkpoint.get("research_coverage") if isinstance(checkpoint, dict) else None
    if not isinstance(coverage, dict):
        return {"limitation": default_limitation, "accepted_items": None, "rows": []}
    per_keyword = coverage.get("per_keyword")
    rows: list[JsonObject]
    if not isinstance(per_keyword, list) or not per_keyword:
        rows = [coverage]
    else:
        shared = {
            key: value
            for key, value in coverage.items()
            if key not in {"keywords", "per_keyword", "visible_count", "accepted_count"}
        }
        rows = [{**shared, **item} for item in per_keyword if isinstance(item, dict)]
    return {
        "limitation": coverage.get("limitation"),
        "accepted_items": coverage.get("accepted_count"),
        "rows": rows,
    }


def _public_status(status: str) -> str:
    return {
        "pending": "queued",
        "success": "completed",
        "skipped": "completed",
    }.get(status, status)
