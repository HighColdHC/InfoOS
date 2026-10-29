"""Worker-side acquisition for one durable manual-capture observation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.input_versions import card_build_input_version
from backend.application.tasks.planner import raw_item_target_id, raw_item_task_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.application.usecases.public_documents import (
    acquire_public_document_from_environment,
)
from backend.domain.captures import (
    canonical_capture_url,
    capture_card_url,
    content_identity_hash,
)
from backend.domain.models.raw_item import RawItem
from backend.domain.public_documents import PublicDocumentRequest
from backend.infrastructure.storage.base import dumps, utc_now
from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository
from backend.infrastructure.storage.acquisition_checkpoints import (
    AcquisitionCheckpointRepository,
)
from backend.infrastructure.storage.database import connect_writable
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.raw_items import RawItemRepository


_MAX_FETCH_BYTES = 5 * 1024 * 1024
_USER_AGENT = "InfoOS/0.1 manual-capture"


def fetch_manual_capture(base_dir: Path, task: Task) -> dict[str, Any]:
    capture_id = str(task.artifacts_summary["capture_id"])
    connector_id = str(task.artifacts_summary["connector_id"])
    raw_item_id = str(task.artifacts_summary["raw_item_id"])
    raw_repo = RawItemRepository(base_dir)
    raw = raw_repo.get_model_for_connector(connector_id, raw_item_id)
    if raw is None:
        raise RuntimeError("capture_raw_item_missing")

    provider_evidence: dict[str, object] | None = None
    if str(task.artifacts_summary.get("capture_adapter")) == "douyin:item":
        acquired = _fetch_douyin(raw)
    elif str(task.artifacts_summary.get("capture_adapter")) == "github:item":
        acquired = _fetch_github(raw)
    elif str(task.artifacts_summary.get("capture_mode")) == "readable_content":
        acquired = raw
    else:
        acquired, provider_evidence = _fetch_public_web(raw)
    if not (acquired.raw_text or "").strip():
        raise RuntimeError("capture_readable_content_empty")
    resolved_card_url = capture_card_url(
        acquired.source_platform,
        canonical_capture_url(acquired.source_url),
    )
    acquired = acquired.model_copy(update={"source_url": resolved_card_url})

    receipt_repo = CaptureReceiptRepository(base_dir)
    current = receipt_repo.get(capture_id)
    if current is None:
        raise RuntimeError("capture_receipt_missing")
    acquired_metadata = acquired.raw_metadata
    identity_evidence = {
        key: value
        for key, value in {
            "sanitized_readable_html": acquired_metadata.get(
                "sanitized_readable_html"
            ),
            "images": acquired_metadata.get("images"),
            "videos": acquired_metadata.get("videos"),
        }.items()
        if value is not None
    }
    current_hash = content_identity_hash(
        acquired.raw_text,
        identity_evidence,
    )
    previous = receipt_repo.latest_before_observation(
        str(current["canonical_identity_hash"]),
        int(current["observation_version"]),
    )
    existing_card = receipt_repo.card_for_url(resolved_card_url)
    unchanged = bool(
        previous
        and previous.get("content_identity_hash") == current_hash
        and existing_card
        and existing_card.get("status") == "active"
    )
    disposition = "unchanged" if unchanged else (
        "refresh" if int(current["observation_version"]) > 1 or existing_card else "new"
    )
    card_build_tasks: list[Task] = []
    if not unchanged:
        input_version = card_build_input_version(
            {
                "raw_item_id": acquired.raw_item_id,
                "source_url": acquired.source_url,
                "raw_text": acquired.raw_text,
                "published_at": (
                    acquired.raw_timestamps.published_at
                    if acquired.raw_timestamps
                    else None
                ),
                "raw_metadata": acquired.raw_metadata,
            }
        )
        card_build_tasks.append(
            Task(
                task_id=raw_item_task_id(
                    TaskType.CARD_BUILD,
                    acquired.raw_item_id,
                    connector_id=acquired.connector_id,
                    input_version=input_version,
                ),
                task_type=TaskType.CARD_BUILD,
                target_kind="raw_item",
                target_id=raw_item_target_id(
                    acquired.connector_id,
                    acquired.raw_item_id,
                ),
                input_version=input_version,
                depends_on=(
                    DependencySpec(
                        task_id=task.task_id,
                        required_status=TaskStatus.SUCCESS,
                    ),
                ),
                artifacts_summary={
                    "capture_id": capture_id,
                    "source_id": current["source_id"],
                    "run_id": current["run_id"],
                    "chain_id": task.artifacts_summary.get("chain_id"),
                    "chain_root_task_id": task.task_id,
                },
            )
        )

    now = utc_now()
    checkpoint = {
        **task.artifacts_summary,
        "source_id": current["source_id"],
        "run_id": current["run_id"],
        "source_input_version": task.input_version,
        "initial_fetch": False,
        "calibration_evidence_id": None,
        "committed_last_run_at": now,
        "committed_next_run_at": now,
        "raw_item_ids": [acquired.raw_item_id],
        "card_build_task_ids": [item.task_id for item in card_build_tasks],
        "cursor_after": None,
        **(
            {"public_document_evidence": provider_evidence}
            if provider_evidence is not None
            else {}
        ),
    }
    conn = connect_writable(resolve_sqlite_path(base_dir))
    try:
        conn.execute("begin immediate")
        RawItemRepository(base_dir).upsert_raw_items(
            [acquired],
            run_id=str(current["run_id"]),
            conn=conn,
        )
        TaskRepository(str(resolve_sqlite_path(base_dir))).enqueue_many(
            card_build_tasks,
            conn=conn,
        )
        AcquisitionCheckpointRepository.upsert_to_connection(
            conn,
            task_id=task.task_id,
            source_id=str(current["source_id"]),
            run_id=str(current["run_id"]),
            source_input_version=task.input_version,
            initial_fetch=False,
            calibration_evidence_id=None,
            committed_last_run_at=now,
            committed_next_run_at=now,
            payload=checkpoint,
        )
        conn.execute(
            """
            update capture_receipts
            set content_identity_hash = ?, disposition = ?, processing_status = ?,
                canonical_url = ?, card_id = coalesce(card_id, ?), updated_at = ?
            where capture_id = ?
            """,
            (
                current_hash,
                disposition,
                "succeeded" if unchanged else "running",
                resolved_card_url,
                existing_card.get("card_id") if existing_card else None,
                now,
                capture_id,
            ),
        )
        conn.execute(
            """
            update run_history
            set status = 'success', fetched_count = 1, new_count = ?,
                dedup_skipped_count = ?, decision = ?, finished_at = ?,
                artifacts_summary = ?
            where run_id = ?
            """,
            (
                0 if unchanged else 1,
                1 if unchanged else 0,
                disposition,
                now,
                dumps(
                    {
                        **checkpoint,
                    }
                ),
                current["run_id"],
            ),
        )
        conn.execute("commit")
    except Exception:
        if conn.in_transaction:
            conn.execute("rollback")
        raise
    finally:
        conn.close()

    return {
        "capture_id": capture_id,
        "run_id": current["run_id"],
        "raw_item_ids": [acquired.raw_item_id],
        "card_build_task_ids": [item.task_id for item in card_build_tasks],
        "content_identity_hash": current_hash,
        "disposition": disposition,
        "source_id": current["source_id"],
        "source_input_version": task.input_version,
        "initial_fetch": False,
        "calibration_evidence_id": None,
        "committed_last_run_at": now,
        "committed_next_run_at": now,
        "cursor_after": None,
        "acquisition_checkpoint": checkpoint,
    }


def _fetch_public_web(raw: RawItem) -> tuple[RawItem, dict[str, object]]:
    acquired = acquire_public_document_from_environment(
        PublicDocumentRequest(
            url=raw.source_url,
            headers={"User-Agent": _USER_AGENT},
            timeout_seconds=20,
            max_bytes=_MAX_FETCH_BYTES,
        )
    )
    if acquired.document is None:
        failure = acquired.provider_evidence.get("primary", {})
        failure_class = (
            str(failure.get("failure_class") or "unknown")
            if isinstance(failure, dict)
            else "unknown"
        )
        raise RuntimeError(f"capture_public_fetch_failed:{failure_class}")
    document = acquired.document
    metadata = dict(raw.raw_metadata)
    metadata.update(
        {
            "sanitized_readable_html": document.readable_html,
            "fetch_final_url": document.final_url,
            **({"images": list(document.images)} if document.images else {}),
            **({"videos": list(document.videos)} if document.videos else {}),
        }
    )
    return (
        raw.model_copy(
            update={
                "source_url": document.final_url,
                "raw_text": document.content,
                "raw_metadata": metadata,
            }
        ),
        acquired.provider_evidence,
    )


def _fetch_douyin(raw: RawItem) -> RawItem:
    from backend.infrastructure.connectors.douyin.item import DouyinItemConnector

    result = DouyinItemConnector(
        connector_id=raw.connector_id,
        identifier=raw.source_url,
    ).fetch_new_items(None)
    if not result.items:
        raise RuntimeError("capture_douyin_item_empty")
    fetched = result.items[0]
    metadata = {**raw.raw_metadata, **fetched.raw_metadata}
    metadata["capture_adapter"] = "douyin:item"
    return fetched.model_copy(
        update={
            "raw_item_id": raw.raw_item_id,
            "connector_id": raw.connector_id,
            "raw_metadata": metadata,
            "captured_at": raw.captured_at,
        }
    )


def _fetch_github(raw: RawItem) -> RawItem:
    from backend.infrastructure.connectors.github import GitHubItemConnector

    fetched = GitHubItemConnector(
        connector_id=raw.connector_id, identifier=raw.source_url,
    ).fetch_new_items(None).items[0]
    return fetched.model_copy(update={
        "raw_item_id": raw.raw_item_id,
        "source_type": raw.source_type,
        "captured_at": raw.captured_at,
        "raw_metadata": {
            **{key: value for key, value in raw.raw_metadata.items()
               if key not in {"images", "videos", "sanitized_readable_html"}},
            **fetched.raw_metadata,
        },
    })
