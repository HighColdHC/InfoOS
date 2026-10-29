"""Submit and project explicit browser captures onto the durable collector chain."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.application.usecases.agent_capture import find_existing_capture
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.domain.captures import (
    capture_identity_hash,
    capture_card_url,
    capture_payload_hash,
    canonical_capture_url,
    capture_media_urls,
    content_identity_hash,
    sanitize_readable_html,
)
from backend.domain.agent_collect import MCP_COLLECT_WINDOW_HOURS
from backend.domain.models.raw_item import RawItem
from backend.domain.sources.registry import (
    capture_adapter_for_platform,
    capture_platform_for_host,
)
from backend.infrastructure.connectors.github import supported_github_target
from backend.infrastructure.storage.base import dumps, utc_now
from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository
from backend.infrastructure.storage.tasks import operations as task_storage
from backend.infrastructure.storage.database import connect_writable, ensure_database_initialized
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.runs import RunRepository


class CaptureIdempotencyConflict(ValueError):
    pass


class CaptureDailyLimitExceeded(ValueError):
    def __init__(self, limit: int):
        super().__init__("捕获次数已达到滚动窗口上限")
        self.limit = limit
        self.window_hours = MCP_COLLECT_WINDOW_HOURS


@dataclass(frozen=True)
class CaptureSubmission:
    receipt: dict[str, Any]
    status_code: int


def submit_manual_capture(
    base_dir: Path | str,
    *,
    token_id: str,
    payload: dict[str, Any],
    daily_limit: int | None = None,
    idempotency_ignore_captured_at: bool = False,
    deduplicate: bool | None = None,
) -> CaptureSubmission:
    base_path = Path(base_dir)
    db_path = ensure_database_initialized(resolve_sqlite_path(base_path))
    # MCP generates the timestamp on each call; it is not caller intent.
    # Browser capture retains its existing full-payload idempotency contract.
    request_hash = capture_payload_hash(
        {key: value for key, value in payload.items() if key != "captured_at"}
        if idempotency_ignore_captured_at else payload
    )
    client_capture_id = str(payload["client_capture_id"])
    requested_url = canonical_capture_url(str(payload["url"]))
    canonical_url = canonical_capture_url(
        str(payload.get("canonical_url") or requested_url)
    )
    platform = capture_platform_for_host(_hostname(canonical_url))
    canonical_url = capture_card_url(platform, canonical_url)
    adapter = capture_adapter_for_platform(platform)
    if adapter == "github:item" and not supported_github_target(canonical_url):
        adapter = "web"
    # None keeps the browser extension's legacy identity/refresh semantics.
    # MCP passes False for explicit refresh and True for normal deduplication.
    identity_hash = capture_identity_hash(platform, canonical_url, extended=deduplicate is not None)
    receipt_repo = CaptureReceiptRepository(base_path, db_path=db_path)

    rendered = payload.get("rendered_content")
    sanitized_html = ""
    readable_text = ""
    capture_media: dict[str, list[str]] = {}
    if isinstance(rendered, dict):
        rendered_html = str(rendered.get("html") or "")
        sanitized_html, extracted = sanitize_readable_html(
            rendered_html,
            base_url=canonical_url,
        )
        capture_media = capture_media_urls(rendered_html, base_url=canonical_url)
        readable_text = str(rendered.get("text") or "").strip() or extracted
    content_hash = (
        content_identity_hash(
            readable_text,
            {
                **(
                    {"sanitized_readable_html": sanitized_html}
                    if sanitized_html
                    else {}
                ),
                **capture_media,
            },
        )
        if str(payload.get("capture_mode")) == "readable_content"
        else None
    )

    conn = connect_writable(db_path)
    try:
        conn.execute("begin immediate")
        if deduplicate:
            existing = find_existing_capture(base_path, str(payload["url"]), connection=conn)
            if existing:
                conn.execute("commit")
                return CaptureSubmission(existing, 200)
        now = utc_now()
        replay = receipt_repo.get_for_client(
            token_id,
            client_capture_id,
            connection=conn,
        )
        if replay is not None:
            if str(replay["payload_hash"]) != request_hash:
                raise CaptureIdempotencyConflict("Idempotency key was reused with a different payload")
            conn.execute("commit")
            return CaptureSubmission(_public_receipt(replay, replay=True), 200)

        if daily_limit is not None:
            since = (datetime.fromisoformat(now.replace("Z", "+00:00"))
                     - timedelta(hours=MCP_COLLECT_WINDOW_HOURS)).isoformat().replace("+00:00", "Z")
            if receipt_repo.count_for_token(
                token_id, since=since, until=now, connection=conn,
            ) >= daily_limit:
                raise CaptureDailyLimitExceeded(daily_limit)

        previous = receipt_repo.latest_for_identity(identity_hash, connection=conn)
        existing_card = receipt_repo.card_for_url(canonical_url, connection=conn)
        observation_version = (previous["observation_version"] if previous is not None else 0) + 1
        unchanged = bool(
            content_hash
            and previous
            and str(previous.get("content_identity_hash") or "") == content_hash
            and existing_card
            and existing_card["status"] == "active"
        )
        disposition = "unchanged" if unchanged else (
            "refresh" if previous or existing_card else "new"
        )
        capture_id = f"cap_{uuid4().hex}"
        run_id = f"capture_run_{uuid4().hex}"
        source_id = f"browser-inbox::{platform}"
        connector_id = source_id
        raw_item_id = capture_id
        task_id = None if unchanged else f"capture_fetch_{uuid4().hex}"
        card_id = str(existing_card["card_id"]) if existing_card else None
        processing_status = "succeeded" if unchanged else "pending"

        _ensure_system_source(
            conn,
            source_id=source_id,
            connector_id=connector_id,
            platform=platform,
            now=now,
        )
        raw_item = RawItem(
            raw_item_id=raw_item_id,
            connector_id=connector_id,
            source_platform=platform,
            source_type="capture",
            source_url=canonical_url,
            captured_at=str(payload["captured_at"]),
            raw_text=readable_text or str(payload.get("selected_text") or "").strip() or None,
            raw_metadata={
                "source_id": source_id,
                "title": str(payload.get("title") or ""),
                "capture_id": capture_id,
                "capture_channel": "browser_extension",
                "capture_mode": str(payload["capture_mode"]),
                "capture_adapter": adapter,
                "client_capture_id": client_capture_id,
                "client": dict(payload.get("client") or {}),
                "page_language": payload.get("page_language"),
                "original_url": requested_url,
                "canonical_identity_hash": identity_hash,
                "content_identity_hash": content_hash,
                "observation_version": observation_version,
                "selected_text": payload.get("selected_text"),
                "sanitized_readable_html": sanitized_html or None,
                **capture_media,
            },
        )
        RawItemRepository(base_path, db_path=db_path).upsert_raw_items(
            [raw_item],
            run_id=run_id,
            conn=conn,
        )
        run_artifacts = {
            "capture_id": capture_id,
            "raw_item_ids": [raw_item_id],
            "canonical_identity_hash": identity_hash,
            "observation_version": observation_version,
        }
        RunRepository.append_to_connection(
            conn,
            {
                "run_id": run_id,
                "source_id": source_id,
                "connector_id": connector_id,
                "scheduled_for": now,
                "started_at": now,
                "finished_at": now,
                "status": "success" if unchanged else "pending",
                "fetched_count": 1 if readable_text else 0,
                "visible_count": 1,
                "new_count": 0 if unchanged else 1,
                "dedup_skipped_count": 1 if unchanged else 0,
                "trigger": "browser_capture",
                "decision": disposition,
                "raw_payload": {
                    "capture_id": capture_id,
                    "capture_mode": payload["capture_mode"],
                    "platform": platform,
                },
                "task_type": "fetch",
                "target_kind": "source",
                "target_id": source_id,
                "artifacts_summary": run_artifacts,
            },
        )
        if task_id is not None:
            TaskRepository(str(db_path)).enqueue_many(
                [
                    Task(
                        task_id=task_id,
                        task_type=TaskType.FETCH,
                        target_kind="source",
                        target_id=source_id,
                        input_version=request_hash,
                        artifacts_summary={
                            **run_artifacts,
                            "run_id": run_id,
                            "chain_id": f"capture_chain_{capture_id}",
                            "chain_root_task_id": task_id,
                            "raw_item_id": raw_item_id,
                            "connector_id": connector_id,
                            "capture_mode": payload["capture_mode"],
                            "capture_adapter": adapter,
                            "queue_platform": platform if adapter != "web" else "web",
                        },
                    )
                ],
                conn=conn,
            )
        stored = {
            "capture_id": capture_id,
            "client_capture_id": client_capture_id,
            "token_id": token_id,
            "payload_hash": request_hash,
            "canonical_identity_hash": identity_hash,
            "content_identity_hash": content_hash,
            "observation_version": observation_version,
            "capture_mode": payload["capture_mode"],
            "disposition": disposition,
            "processing_status": processing_status,
            "canonical_url": canonical_url,
            "source_id": source_id,
            "run_id": run_id,
            "raw_connector_id": connector_id,
            "raw_item_id": raw_item_id,
            "task_id": task_id,
            "card_id": card_id,
            "error_code": None,
            "created_at": now,
            "updated_at": now,
        }
        CaptureReceiptRepository.insert_to_connection(conn, stored)
        conn.execute("commit")
    except Exception:
        if conn.in_transaction:
            conn.execute("rollback")
        raise
    finally:
        conn.close()

    return CaptureSubmission(_public_receipt(stored), 200 if unchanged else 202)


def read_manual_capture(
    base_dir: Path | str,
    *,
    capture_id: str,
    token_id: str | None = None,
) -> dict[str, Any] | None:
    repo = CaptureReceiptRepository(base_dir)
    stored = repo.get_owned(capture_id, token_id) if token_id else repo.get(capture_id)
    if stored is None:
        return None
    return _project_receipt(repo, stored)


def _project_receipt(
    repo: CaptureReceiptRepository,
    stored: dict[str, Any],
) -> dict[str, Any]:
    projected = dict(stored)
    capture_id = str(stored["capture_id"])
    task_rows: list[Any] = []
    if stored.get("task_id"):
        with repo.connect_existing() as conn:
            task_rows = task_storage.capture_task_rows(conn, str(stored["task_id"]), capture_id)
        statuses = [str(row["status"]) for row in task_rows]
        if task_rows:
            projected["updated_at"] = str(
                task_rows[-1]["updated_at"] or projected["updated_at"]
            )
        if "running" in statuses or (len(task_rows) > 1 and "pending" in statuses):
            projected["processing_status"] = "running"
        elif "pending" in statuses:
            projected["processing_status"] = "pending"
        elif any(status in {"failed", "blocked", "skipped"} for status in statuses):
            projected["processing_status"] = "failed"
            projected["error_code"] = "capture_processing_failed"
    card = repo.card_for_url(str(stored["canonical_url"]))
    if card is not None:
        projected["card_id"] = str(card["card_id"])
        if card.get("status") == "active" and (
            projected.get("processing_status") != "failed"
            or card.get("source_run_id") == stored.get("run_id")
        ):
            projected["processing_status"] = "succeeded"
            projected["error_code"] = None
            projected["updated_at"] = str(card.get("updated_at") or projected["updated_at"])
    return _public_receipt(projected)


def _public_receipt(stored: dict[str, Any], *, replay: bool = False) -> dict[str, Any]:
    processing = stored["processing_status"]
    disposition = "idempotent_replay" if replay else str(stored["disposition"])
    status = "duplicate" if disposition in {"unchanged", "idempotent_replay"} else "accepted"
    capture_id = str(stored["capture_id"])
    return {
        "schema": "infoos.capture-receipt.v1",
        "capture_id": capture_id,
        "status": status,
        "disposition": disposition,
        "processing_status": processing,
        "raw_item_id": stored.get("raw_item_id"),
        "task_id": stored.get("task_id"),
        "card_id": stored.get("card_id"),
        "infoos_url": f"/?menu=cards&capture_id={capture_id}",
        "error_code": stored.get("error_code"),
        "created_at": stored.get("created_at"),
        "updated_at": stored.get("updated_at"),
    }


def _ensure_system_source(
    conn: Any,
    *,
    source_id: str,
    connector_id: str,
    platform: str,
    now: str,
) -> None:
    conn.execute(
        """
        insert into sources (
          source_id, platform, source_type, identifier, display_name,
          cadence_hours, notes, enabled, connector_status, connector_id,
          raw_metadata, next_run_at, last_run_at, created_at, updated_at
        ) values (?, ?, 'capture', ?, ?, 876000, ?, 0, 'supported', ?, ?, null, null, ?, ?)
        on conflict(source_id) do update set updated_at = excluded.updated_at
        """,
        (
            source_id,
            platform,
            source_id,
            f"浏览器收件箱 · {platform}",
            "System-managed capture-only source",
            connector_id,
            dumps({"system_managed": True, "capture_only": True}),
            now,
            now,
        ),
    )


def _hostname(url: str) -> str:
    from urllib.parse import urlsplit

    return str(urlsplit(url).hostname or "")
