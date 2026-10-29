"""Durably commit a fetched page before its card-building work is scheduled."""
from __future__ import annotations

from backend.domain.timestamps import utc_now_seconds_z

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.application.tasks.input_versions import card_build_input_version
from backend.application.tasks.planner import raw_item_target_id, raw_item_task_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.domain.sources.versioning import source_fetch_input_version
from backend.domain.sources.capability import resolve_source_capability
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.database import connect_writable, ensure_database_initialized
from backend.infrastructure.storage.base import loads
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.acquisition_checkpoints import AcquisitionCheckpointRepository
from backend.infrastructure.storage.runs import RunRepository
from backend.infrastructure.storage.source_calibrations import SourceCalibrationRepository
from backend.infrastructure.storage.source_cursors import SourceCursorRepository
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.tasks import operations as task_storage


@dataclass(frozen=True)
class FetchCommitResult:
    raw_item_ids: list[str]
    card_build_task_ids: list[str]
    cursor_value: str | None
    checkpoint: dict[str, Any]


class SourceConfigurationChangedError(RuntimeError):
    """The connector result no longer belongs to the current source version."""


def commit_fetch_result(
    base_dir: Path | str,
    *,
    source: dict[str, Any],
    run_id: str,
    raw_items: list[RawItem],
    next_cursor: str | None,
    chain_id: str,
    chain_root_task_id: str,
    task_id: str | None = None,
    worker_id: str | None = None,
    task_attempt: int | None = None,
    checkpoint_context: dict[str, Any] | None = None,
    expected_source_version: str | None = None,
    initial_fetch: bool = False,
    run_payload: dict[str, Any] | None = None,
    committed_last_run_at: str | None = None,
    committed_next_run_at: str | None = None,
    research_fetch: bool = False,
) -> FetchCommitResult:
    """Commit every durable acquisition effect in one SQLite transaction."""
    db_path = resolve_sqlite_path(base_dir)
    ensure_database_initialized(db_path)
    raw_item_repo = RawItemRepository(base_dir, db_path=db_path)
    cursor_repo = SourceCursorRepository(base_dir, db_path=db_path)
    task_repo = TaskRepository(str(db_path))
    source_id = str(source["source_id"])
    connector_id = str(source["connector_id"])
    source_input_version = expected_source_version or source_fetch_input_version(source)
    raw_item_ids = [raw_item.raw_item_id for raw_item in raw_items]
    durable_parent_task_id = (
        task_id
        if task_id and worker_id is not None and task_attempt is not None
        else None
    )
    card_build_tasks = []
    for raw_item in raw_items:
        input_version = _raw_item_input_version(raw_item)
        card_build_tasks.append(
            Task(
                task_id=raw_item_task_id(
                    TaskType.CARD_BUILD,
                    raw_item.raw_item_id,
                    connector_id=raw_item.connector_id,
                    input_version=input_version,
                ),
                task_type=TaskType.CARD_BUILD,
                target_kind="raw_item",
                target_id=raw_item_target_id(raw_item.connector_id, raw_item.raw_item_id),
                input_version=input_version,
                depends_on=(
                    DependencySpec(
                        task_id=durable_parent_task_id,
                        required_status=TaskStatus.SUCCESS,
                    ),
                ) if durable_parent_task_id else (),
                artifacts_summary={
                    "source_id": source_id,
                    "run_id": run_id,
                    **({"initial_fetch": True} if initial_fetch else {}),
                    "chain_id": chain_id,
                    "chain_root_task_id": chain_root_task_id,
                },
            )
        )
    card_build_task_ids = [task.task_id for task in card_build_tasks]
    finished_at = committed_last_run_at or utc_now_seconds_z()
    next_run_at = (
        None
        if research_fetch
        else committed_next_run_at or finished_at
    )
    calibration_evidence_id = (
        f"initial_fetch_{task_id}_{max(1, int(task_attempt or 1))}"
        if initial_fetch and task_id
        else None
    )
    checkpoint = {
        **(checkpoint_context or {}),
        "source_id": source_id,
        "connector_id": connector_id,
        "run_id": run_id,
        "raw_item_ids": raw_item_ids,
        "raw_item_count": len(raw_item_ids),
        "discovered_link_count": (checkpoint_context or {}).get("discovered_link_count"),
        "dedup_skipped_count": (checkpoint_context or {}).get("dedup_skipped_count"),
        "discovered_links": list((checkpoint_context or {}).get("discovered_links") or [])[:50],
        "discovery_availability": str(
            (checkpoint_context or {}).get("discovery_availability") or "unavailable"
        ),
        "discovery_evidence": dict(
            (checkpoint_context or {}).get("discovery_evidence") or {"availability": "unavailable"}
        ),
        "card_build_task_ids": card_build_task_ids,
        "cursor_after": next_cursor,
        "initial_fetch": initial_fetch,
        "no_data": not raw_item_ids,
        "source_input_version": source_input_version,
        "calibration_evidence_id": calibration_evidence_id,
        "committed_last_run_at": finished_at,
        "committed_next_run_at": next_run_at,
    }

    conn = connect_writable(db_path)
    try:
        conn.execute("begin immediate")
        current_row = conn.execute(
            "select * from sources where source_id = ?",
            (source_id,),
        ).fetchone()
        if current_row is None or _stored_source_version(current_row) != source_input_version:
            raise SourceConfigurationChangedError("source_configuration_changed")
        if current_row["calibration_input_version"] != source_input_version:
            # The transaction above validated the live config. Refresh this
            # derived cache so the schedule/promotion updates below also match.
            conn.execute(
                "update sources set calibration_input_version = ? where source_id = ?",
                (source_input_version, source_id),
            )
        changed_items = raw_item_repo.upsert_raw_items_with_changes(raw_items, run_id=run_id, conn=conn)
        changed_targets = {
            raw_item_target_id(item.connector_id, item.raw_item_id) for item in changed_items
        }
        observation_targets = {raw_item_target_id(item.connector_id, item.raw_item_id)
                               for item in raw_items if item.raw_metadata.get("_zsxq_digest_observation")}
        card_build_tasks = [task for task in card_build_tasks
                           if task.target_id not in observation_targets or task.target_id in changed_targets]
        card_build_task_ids = [task.task_id for task in card_build_tasks]
        checkpoint["card_build_task_ids"] = card_build_task_ids
        # Unchanged raw items keep their existing CARD_BUILD ids in the checkpoint
        # for batch linkage; only real content changes enqueue new work.
        task_repo.enqueue_many(
            [task for task in card_build_tasks if task.target_id in changed_targets],
            conn=conn,
        )
        if not research_fetch:
            cursor_repo.upsert(
                source_id=source_id,
                connector_id=connector_id,
                cursor_value=next_cursor,
                last_run_id=run_id,
                conn=conn,
            )
        if initial_fetch:
            SourceCalibrationRepository.append_to_connection(
                conn,
                calibration_id=str(calibration_evidence_id),
                source_id=source_id,
                platform=str(current_row["platform"]),
                source_type=str(current_row["source_type"]),
                identifier=str(current_row["identifier"]),
                result="success",
                task_id=str(task_id),
                attempt=max(1, int(task_attempt or 1)),
                visible_count=int(checkpoint.get("visible_count") or 0),
                sample_count=len(raw_item_ids),
                raw_payload={
                    "mode": "initial_fetch",
                    "source_input_version": source_input_version,
                    "run_id": run_id,
                    "no_data": not raw_item_ids,
                },
            )
            capability = resolve_source_capability(dict(current_row))
            promoted_status = "partial" if capability.status == "partial" and capability.known_gap else "supported"
            conn.execute(
                """
                update sources set connector_status = ?, last_run_at = ?, next_run_at = ?, updated_at = ?
                where source_id = ? and calibration_input_version = ?
                """,
                (promoted_status, finished_at, next_run_at, finished_at, source_id, source_input_version),
            )
        elif not research_fetch:
            conn.execute(
                """
                update sources set last_run_at = ?, next_run_at = ?, updated_at = ?
                where source_id = ? and calibration_input_version = ?
                """,
                (finished_at, next_run_at, finished_at, source_id, source_input_version),
            )
        if run_payload is not None:
            RunRepository.append_to_connection(
                conn,
                {**run_payload, "artifacts_summary": checkpoint, "derived_task_ids": card_build_task_ids},
            )
        if task_id and worker_id is not None and task_attempt is not None:
            updated = task_storage.update_fetch_checkpoint_artifacts(
                conn, task_id=task_id, worker_id=worker_id,
                attempt=task_attempt, checkpoint=checkpoint,
            )
            if not updated:
                raise RuntimeError(f"fetch_checkpoint_lease_lost:{task_id}")
            AcquisitionCheckpointRepository.upsert_to_connection(
                conn,
                task_id=task_id,
                source_id=source_id,
                run_id=run_id,
                source_input_version=source_input_version,
                initial_fetch=initial_fetch,
                calibration_evidence_id=calibration_evidence_id,
                committed_last_run_at=finished_at,
                committed_next_run_at=next_run_at,
                payload=checkpoint,
            )
        TaskEventRepository.append_to_connection(
            conn,
            event_type="acquisition_committed",
            task_id=task_id or chain_root_task_id,
            chain_id=chain_id,
            status="success",
            payload={
                "source_ids": [source_id],
                "run_id": run_id,
                "raw_item_count": len(raw_item_ids),
                "discovered_link_count": checkpoint.get("discovered_link_count"),
                "dedup_skipped_count": checkpoint.get("dedup_skipped_count"),
                "card_build_task_count": len(card_build_task_ids),
            },
        )
        conn.execute("commit")
    except Exception:
        if conn.in_transaction:
            conn.execute("rollback")
        raise
    finally:
        conn.close()

    return FetchCommitResult(
        raw_item_ids=raw_item_ids,
        card_build_task_ids=card_build_task_ids,
        cursor_value=next_cursor,
        checkpoint=checkpoint,
    )


def _raw_item_input_version(raw_item: RawItem) -> str:
    return card_build_input_version(
        {
            "raw_item_id": raw_item.raw_item_id,
            "source_url": raw_item.source_url,
            "raw_text": raw_item.raw_text,
            "published_at": raw_item.raw_timestamps.published_at if raw_item.raw_timestamps else None,
            "raw_metadata": raw_item.raw_metadata,
        }
    )




def _stored_source_version(row: Any) -> str:
    # Calibration records the rules at calibration time. A code-only rule
    # upgrade can change the fetch fingerprint without changing this cache.
    # Compare the transaction's current config using the same rules as admission.
    payload = dict(row)
    payload["raw_metadata"] = loads(payload.get("raw_metadata"), {})
    payload["enabled"] = bool(payload.get("enabled"))
    return source_fetch_input_version(payload)
