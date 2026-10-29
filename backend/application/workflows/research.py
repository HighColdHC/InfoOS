"""Atomic creation of a multi-platform research batch."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from backend.application.tasks.batches import persist_task_batch
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.domain.research import (
    ResearchRequestError,
    idempotency_key_hash,
    normalize_research_request,
    research_request_hash,
)
from backend.domain.agent_collect import MCP_COLLECT_WINDOW_HOURS
from backend.domain.sources.versioning import source_fetch_input_version
from backend.infrastructure.storage.database import connect, ensure_database_initialized
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.research_runs import (
    count_token_research_runs,
    find_research_batch_by_idempotency_hash,
)
from backend.infrastructure.storage.repositories import utc_now


def create_research_run(
    base_dir: Path | str,
    *,
    payload: dict[str, Any],
    idempotency_key: str,
    origin: dict | None = None,
    daily_limit: int | None = None,
) -> tuple[str, bool]:
    """Create sources, root tasks and the batch in one BEGIN IMMEDIATE."""
    request = normalize_research_request(payload)
    request_hash = research_request_hash(request)
    key_hash = idempotency_key_hash(idempotency_key)
    base_path = Path(base_dir)
    db_path = resolve_sqlite_path(base_path)
    ensure_database_initialized(db_path)
    source_repo = SourceRepository(base_path, db_path=db_path)
    task_repo = TaskRepository(str(db_path))
    batch_id = f"research_{uuid4().hex[:12]}"

    with connect(db_path) as conn:
        conn.execute("begin immediate")
        now = utc_now()
        existing = find_research_batch_by_idempotency_hash(conn, key_hash)
        if existing is not None:
            stored_hash = str(existing["metadata"].get("request_hash") or "")
            if stored_hash != request_hash:
                raise ResearchRequestError(
                    "idempotency_conflict",
                    "Idempotency-Key was already used with a different request",
                    details={"research_run_id": existing["batch_id"]},
                )
            return str(existing["batch_id"]), True

        if daily_limit is not None:
            token_id = str((origin or {}).get("token_id") or "")
            if not token_id:
                raise ResearchRequestError("invalid_request", "daily_limit requires origin.token_id")
            since = (datetime.fromisoformat(now.replace("Z", "+00:00"))
                     - timedelta(hours=MCP_COLLECT_WINDOW_HOURS)).isoformat().replace("+00:00", "Z")
            if count_token_research_runs(conn, token_id, since=since, until=now) >= daily_limit:
                raise ResearchRequestError(
                    "daily_limit_exceeded", "调研次数已达到滚动窗口上限",
                    details={"limit": daily_limit, "window_hours": MCP_COLLECT_WINDOW_HOURS},
                )

        source_payloads = [
            _research_source_payload(
                conn,
                batch_id=batch_id,
                request=request,
                platform_request=platform_request,
                now=now,
            )
            for platform_request in request["platforms"]
        ]
        sources = [
            source_repo.save_configuration(
                source_payload,
                change_reason="created",
                connection=conn,
            )
            for source_payload in source_payloads
        ]
        tasks = [
            _research_fetch_task(
                source,
                batch_id=batch_id,
                request=request,
                platform_request=platform_request,
                now=now,
            )
            for source, platform_request in zip(
                sources,
                request["platforms"],
                strict=True,
            )
        ]
        task_repo.enqueue_many(tasks, conn=conn)
        persist_task_batch(
            conn,
            batch_id=batch_id,
            batch_type="research",
            task_ids=[task.task_id for task in tasks],
            source_ids=[str(source["source_id"]) for source in sources],
            metadata={
                "schema_version": 1,
                "request": request,
                "request_hash": request_hash,
                "idempotency_key_hash": key_hash,
                "planned_task_count": len(tasks),
                "source_count": len(sources),
                **({"origin": dict(origin)} if origin is not None else {}),
            },
            created_at=now,
        )
    return batch_id, False


def _research_source_payload(
    conn: Any,
    *,
    batch_id: str,
    request: dict[str, Any],
    platform_request: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    platform = str(platform_request["platform"])
    options = platform_request["options"]
    rss_sources = (
        _selected_rss_sources(conn, options.get("source_ids") or [])
        if platform == "rss"
        else []
    )
    source_id = f"{batch_id}_{platform}"
    return {
        "source_id": source_id,
        "source_scope": "research",
        "platform": platform,
        "source_type": "research",
        "identifier": request["name"],
        "display_name": f"{request['name']} · {platform}",
        "cadence_hours": 24,
        "notes": "Hidden runtime source for one research batch",
        "enabled": True,
        "connector_status": "supported",
        # A research source is one durable observation scope.  Reusing a
        # platform-wide connector id would make a later batch overwrite the
        # earlier batch's raw_items row for the same remote item.
        "connector_id": f"research:{batch_id}:{platform}",
        "next_run_at": None,
        "last_run_at": None,
        "created_at": now,
        "raw_metadata": {
            "max_items_per_run": int(platform_request["max_items"]),
            "research": {
                "batch_id": batch_id,
                "keywords": list(request["keywords"]),
                "date_range": request["date_range"],
                "platform_request": platform_request,
                "rss_sources": rss_sources,
            },
        },
    }


def _selected_rss_sources(conn: Any, source_ids: list[str]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for source_id in source_ids:
        row = conn.execute(
            """
            select source_id, identifier, raw_metadata
            from sources
            where source_id = ? and source_scope = 'maintained'
              and platform = 'rss' and source_type = 'feed'
            """,
            (str(source_id),),
        ).fetchone()
        if row is None:
            raise ResearchRequestError(
                "invalid_request",
                f"RSS source is not available: {source_id}",
                details={"platform": "rss", "source_id": str(source_id)},
            )
        import json

        try:
            metadata = json.loads(row["raw_metadata"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        selected.append(
            {
                "source_id": str(row["source_id"]),
                "feed_url": str(row["identifier"]),
                "feed_provider": metadata.get("feed_provider", "generic"),
                "feed_content_mode": metadata.get("feed_content_mode", "feed_preferred"),
            }
        )
    return selected


def _research_fetch_task(
    source: dict[str, Any],
    *,
    batch_id: str,
    request: dict[str, Any],
    platform_request: dict[str, Any],
    now: str,
) -> Task:
    source_id = str(source["source_id"])
    task_id = f"research_fetch_{uuid4().hex[:20]}"
    input_version = source_fetch_input_version(source)
    return Task(
        task_id=task_id,
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id=source_id,
        next_run_at=now,
        input_version=input_version,
        max_attempts=4 if platform_request["platform"] in {"zsxq", "x"} else 3,
        artifacts_summary={
            "source_id": source_id,
            "scheduled_for": now,
            "plan_followup_fetch": False,
            "chain_id": f"research_chain_{batch_id}_{platform_request['platform']}",
            "chain_root_task_id": task_id,
            "enforce_source_capability": True,
            "source_input_version": input_version,
            "research_batch_id": batch_id,
            "research_request": request,
            "research_platform": platform_request["platform"],
        },
    )
