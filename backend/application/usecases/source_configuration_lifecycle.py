"""Persist versioned source configuration and submit Initial Fetch work."""

from __future__ import annotations

from hashlib import sha256

from backend.application.usecases.source_change_actor import SourceChangeActor
from backend.infrastructure.storage.source_change_audit import append_source_change
from pathlib import Path
from typing import Any

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.application.workflows.source_initial_fetch import enqueue_or_reuse_initial_fetch
from backend.domain.sources.configuration_validation import validate_maintained_source_payload
from backend.domain.sources.versioning import source_fetch_input_version
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.source_config_versions import canonical_config_version


_IDENTITY_FIELDS = ("platform", "source_type", "identifier")


class DuplicateSourceError(ValueError):
    """The requested stable platform identity already belongs to a source."""

    def __init__(self, existing_source_id: str):
        super().__init__(f"duplicate source: {existing_source_id}")
        self.existing_source_id = existing_source_id


class ImmutableSourceIdentityError(ValueError):
    """A PUT attempted to mutate stable source identity fields."""

    def __init__(self, fields: tuple[str, ...]):
        super().__init__("immutable source identity: " + ", ".join(fields))
        self.fields = fields


class SourceNotFoundError(LookupError):
    """The requested source configuration does not exist."""

    def __init__(self, source_id: str):
        super().__init__(f"source not found: {source_id}")
        self.source_id = source_id


def create_source_configuration(
    base_dir: Path | str,
    payload: dict[str, Any],
    *,
    now: str,
    actor: SourceChangeActor = SourceChangeActor(),
) -> dict[str, Any]:
    repository = SourceRepository(base_dir)
    with repository.connect() as conn:
        conn.execute("begin immediate")
        normalized = _normalized_create_payload(payload)
        duplicate = repository.find_by_stable_identity(
            normalized["platform"],
            normalized["source_type"],
            normalized["identifier"],
            connection=conn,
        )
        if duplicate is not None:
            raise DuplicateSourceError(str(duplicate["source_id"]))

        source_id = str(normalized.get("source_id") or _generated_source_id(normalized))
        source_id_collision = repository.get_source(source_id, connection=conn)
        if source_id_collision is not None:
            raise DuplicateSourceError(str(source_id_collision["source_id"]))
        candidate = validate_maintained_source_payload(
            {
                **normalized,
                "source_id": source_id,
                "connector_id": normalized.get("connector_id") or source_id,
                "default_domain_id": None,
            }
        )
        stored = repository.save_configuration(candidate, change_reason="created", connection=conn)
        queued = enqueue_or_reuse_initial_fetch(
            base_dir,
            stored,
            now=now,
            reason="source_created",
            connection=conn,
        )
        append_source_change(conn, source_id=source_id, action="create", before=None, after=stored, **actor.as_dict())
    result = source_lifecycle_result(stored, queued)
    return result


def update_source_configuration(
    base_dir: Path | str,
    source_id: str,
    payload: dict[str, Any],
    *,
    now: str,
    actor: SourceChangeActor = SourceChangeActor(),
    allow_identifier_change: bool = False,
) -> dict[str, Any]:
    repository = SourceRepository(base_dir)
    with repository.connect() as conn:
        conn.execute("begin immediate")
        existing = repository.get_maintained_source(source_id, connection=conn)
        if existing is None or bool(existing["raw_metadata"].get("system_managed")):
            raise SourceNotFoundError(source_id)

        merged = _merged_update_payload(existing, payload)
        immutable_changes = tuple(
            field
            for field in _IDENTITY_FIELDS
            if not (allow_identifier_change and field == "identifier")
            and _stable_identity_value(merged.get(field))
            != _stable_identity_value(existing.get(field))
        )
        if immutable_changes:
            raise ImmutableSourceIdentityError(immutable_changes)

        candidate = validate_maintained_source_payload(
            {
                **merged,
                "source_id": source_id,
                "connector_id": existing.get("connector_id"),
                "default_domain_id": None,
            }
        )
        before_fetch_version = source_fetch_input_version(existing)
        after_fetch_version = source_fetch_input_version(candidate)
        fetch_changed = before_fetch_version != after_fetch_version
        if not fetch_changed:
            candidate["connector_status"] = existing.get("connector_status")

        before_config_version = canonical_config_version(existing)
        after_config_version = canonical_config_version(candidate)
        change_reason = _change_reason(
            existing,
            candidate,
            fetch_changed=fetch_changed,
            config_changed=before_config_version != after_config_version,
        )
        stored = repository.save_configuration(candidate, change_reason=change_reason, connection=conn)
        queued = (
            enqueue_or_reuse_initial_fetch(
                base_dir,
                stored,
                now=now,
                reason="fetch_config_changed",
                connection=conn,
            )
            if fetch_changed
            else None
        )
        append_source_change(conn, source_id=source_id, action="update", before=existing, after=stored, **actor.as_dict())
    result = source_lifecycle_result(stored, queued)
    if not fetch_changed:
        _attach_active_task_compatibility(base_dir, stored, result)
    return result


def _normalized_create_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "platform": str(payload.get("platform") or "").strip(),
        "source_type": str(payload.get("source_type") or "").strip(),
        "identifier": str(payload.get("identifier") or "").strip(),
        "display_name": str(payload.get("display_name") or "").strip(),
        "default_domain_id": None,
    }


def _merged_update_payload(
    existing: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    incoming_metadata = payload.get("raw_metadata")
    merged_metadata = {
        **existing["raw_metadata"],
        **(incoming_metadata if isinstance(incoming_metadata, dict) else {}),
    }
    merged = {**existing, **payload, "raw_metadata": merged_metadata}
    if "display_name" in payload:
        merged["display_name"] = str(payload.get("display_name") or "").strip()
    return merged


def _change_reason(
    existing: dict[str, Any],
    candidate: dict[str, Any],
    *,
    fetch_changed: bool,
    config_changed: bool,
) -> str:
    if fetch_changed:
        return "fetch_config_changed"
    if not config_changed:
        return "display_changed"
    changed = {
        field
        for field in ("display_name", "notes", "cadence_hours", "enabled")
        if existing.get(field) != candidate.get(field)
    }
    if changed == {"enabled"}:
        return "activation_changed"
    if changed == {"cadence_hours"}:
        return "schedule_changed"
    return "display_changed"


def source_lifecycle_result(
    source: dict[str, Any],
    queued: dict[str, Any] | None,
) -> dict[str, Any]:
    queue_result = queued or {}
    task_id = str(queue_result["task_id"]) if queue_result.get("task_id") else None
    initial_fetch = bool(queue_result.get("initial_fetch"))
    reused = bool(task_id) and not bool(queue_result.get("created"))
    return {
        "source": source,
        "source_id": str(source["source_id"]),
        "source_run_id": None,
        "task_id": task_id,
        "initial_fetch": initial_fetch,
        "reused": reused,
        # Migration aliases retained for older route clients while the new
        # collector API moves to the uniform fields above.
        "calibration_task_id": task_id,
        "initial_fetch_task_id": task_id if initial_fetch else None,
        "initial_fetch_queue_status": "pending" if initial_fetch else None,
        "queue_status": _source_queue_status(source, task_id),
        "task_ids": [task_id] if task_id else [],
    }


def _generated_source_id(payload: dict[str, Any]) -> str:
    platform = str(payload["platform"])
    source_type = str(payload["source_type"])
    identifier = str(payload["identifier"])
    digest = sha256(f"{platform}\0{source_type}\0{identifier}".encode("utf-8")).hexdigest()[:12]
    return f"{platform}_{source_type}_{digest}"


def _source_queue_status(source: dict[str, Any], task_id: str | None) -> str:
    if source["connector_status"] == "unsupported":
        return "unsupported"
    if not source["enabled"]:
        return "disabled"
    if task_id:
        return "calibration_pending"
    if source["connector_status"] in {"supported", "partial"}:
        return "ready"
    return "not_queued"


def _attach_active_task_compatibility(
    base_dir: Path | str,
    source: dict[str, Any],
    result: dict[str, Any],
) -> None:
    current_version = source_fetch_input_version(source)
    candidates = [
        task
        for task in TaskRepository.for_base_dir(base_dir).list_by_target(
            target_kind="source",
            target_id=str(source["source_id"]),
        )
        if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
        and task.input_version == current_version
        and task.task_type in {TaskType.FETCH, TaskType.SOURCE_CALIBRATE}
    ]
    if not candidates:
        return
    task = min(candidates, key=lambda item: item.task_id)
    is_initial = (
        task.task_type is TaskType.FETCH
        and task.artifacts_summary.get("initial_fetch") is True
    )
    result.update(
        {
            "calibration_task_id": task.task_id,
            "initial_fetch_task_id": task.task_id if is_initial else None,
            "initial_fetch_queue_status": "pending" if is_initial else None,
            "queue_status": "calibration_pending",
            "task_ids": [task.task_id],
        }
    )


def _stable_identity_value(value: Any) -> str:
    return str(value or "").strip().casefold()
