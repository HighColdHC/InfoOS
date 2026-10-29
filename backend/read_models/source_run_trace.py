"""Honest, read-only projection of a Fetch task and its durable lineage."""
from __future__ import annotations

from backend.domain.timestamps import format_utc_seconds_z, parse_utc_or_none

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Mapping, Sequence

from backend.domain.sources.acquisition import acquisition_artifacts
from backend.infrastructure.storage.source_run_json import RunArtifacts, RunCheckpoint, checkpoint_raw_keys, run_count
from backend.infrastructure.storage.source_run_trace_queries_types import RawItemRow, SourceRunSummaryRow, TaskRow, TraceCardRow, TraceHead, TracePage
from backend.read_models.source_run_card_progress import CompleteCardProgress
from backend.read_models.source_run_trace_types import (
    Aggregate, ContentEnrich, Dependency, FetchStats, SafeError, SourceRuns, SourceRunSummary,
    SourceRunTrace, SourceRunWithPipeline, Stage, TraceItem, TraceRun, TrackingRow,
)

from backend.domain.sources.registry import source_task_view
from backend.domain.tasks import RETIRED_TASK_TYPES
from backend.infrastructure.storage.source_run_trace_queries import SourceRunTraceQueries
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.task_event_archive import hot_window_days
from backend.read_models.source_run_card_progress import complete_card_progress
from backend.read_models.source_run_aliases import resolve_source_run_alias
from backend.read_models.source_run_trace_media import (
    project_card_build,
    project_discovery,
    project_final_card,
    project_item_capture,
    project_media,
)
from backend.read_models.source_run_pipeline import project_list_pipelines, project_planned_source_run, project_retired_steps
from backend.read_models.source_run_trace_safety import (
    redact_text,
    opaque_identifier,
    safe_identifier,
    safe_or_opaque_identifier,
    task_identifier_alias,
    safe_label,
    sanitize_external_url,
)
from backend.read_models.task_queue_tracking import TrackingInput, tracking_fields

_TEXT_LIMIT = 4000
_ERROR_LIMIT = 240
_EVENT_EVIDENCE = {"claimed", "started", "completed", "success", "failed", "blocked", "skipped"}
_RAW_PREFIX = "raw_item:v1:"
def read_source_runs(
    base_dir: Path | str,
    *,
    limit: int,
    offset: int,
    time_from: str | None = None,
    time_to: str | None = None,
    status: str | None = None,
    query: str | None = None,
    platform: str | None = None,
    evaluated_at: str | None = None,
) -> SourceRuns:
    now = parse_utc_or_none(evaluated_at) or datetime.now(timezone.utc)
    horizon_end = now + timedelta(hours=24)
    queries = SourceRunTraceQueries(base_dir)
    loaded = queries.list_fetch_runs(
        limit=limit,
        offset=offset,
        now=format_utc_seconds_z(now),
        horizon_end=format_utc_seconds_z(horizon_end),
        time_from=time_from,
        time_to=time_to,
        status=status,
        query=query,
        platform=platform,
    )
    run_rows = [row for row in loaded["rows"] if row["entry_kind"] == "run"]
    if run_rows:
        pipeline_rows = queries.load_list_pipeline_rows(run_rows)
        projections = project_list_pipelines(run_rows, pipeline_rows)
    else:
        projections = {}
    planned_projection = project_planned_source_run()
    items: list[SourceRunWithPipeline] = [
        {**project_source_run_summary(row),
         **(projections[str(row["task_id"])] if row["entry_kind"] == "run" else planned_projection)}
        for row in loaded["rows"]
    ]
    total = loaded["total"]
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "next_offset": offset + limit if offset + len(items) < total else None,
        "planned_horizon_end": format_utc_seconds_z(horizon_end),
    }

def read_source_run_trace(
    base_dir: Path | str,
    source_run_id: str,
    *,
    item_limit: int,
    cursor: str | None,
) -> SourceRunTrace | None:
    start_index = decode_item_cursor(cursor) if cursor else 0
    queries = SourceRunTraceQueries(base_dir)
    fetch_task_id = resolve_source_run_alias(queries, source_run_id)
    if fetch_task_id is None:
        return None
    head = queries.load_trace_head(fetch_task_id)
    if head is None:
        return None
    hot_days = hot_window_days()
    scheduled_at = parse_utc_or_none(head["scheduled_at"])
    events_archived = bool(scheduled_at and scheduled_at < datetime.now(timezone.utc) - timedelta(days=hot_days))
    checkpoint = head["checkpoint"]
    pipeline_rows = queries.load_list_pipeline_rows([head])
    discovery = project_discovery(checkpoint, fetch_task_id)
    chain_id = head["artifacts"]["chain_key"]
    run_events = TaskEventRepository(base_dir).list_events(chain_id=chain_id, limit=10_000) if chain_id else []
    card_build = project_card_build(checkpoint, pipeline_rows, run_events)
    key_roots = _checkpoint_key_roots(checkpoint)
    if start_index > len(key_roots):
        raise ValueError("source-run item cursor out of range")
    selected = key_roots[start_index:start_index + item_limit]
    next_index = start_index + len(selected)
    has_more = next_index < len(key_roots)
    loaded = queries.load_trace_page(
        head,
        root_ids=sorted({root_id for _, root_id in selected if root_id}),
        raw_keys=[key for key, _ in selected],
    )
    items = [_item_projection(base_dir, loaded, key, root_id) for key, root_id in selected]
    fetch_stats: FetchStats = {
        "total_links": discovery["total_links"],
        "dedup_skipped": discovery["dedup_skipped"],
        "pending_items": discovery["pending_items"],
        "raw_item_count": discovery["pending_items"],
    }
    card_progress = complete_card_progress(base_dir, checkpoint, key_roots)
    run: TraceRun = {
        **project_source_run_summary(loaded),
        "root_task_id": _task_id(fetch_task_id),
        "source_name": _clip(loaded["display_name"]) or "unavailable",
        "duration_ms": (_duration(loaded) or 0) * 1000,
        "attempt_count": loaded["attempt"],
        "error": _safe_error(loaded),
        "max_attempts": loaded["max_attempts"],
        "retry_at": loaded["next_run_at"] if loaded["status"] == "pending" and (
            loaded["error_reason"] or loaded["last_failure_class"]
        ) else None,
        "fetch_stats": fetch_stats,
        "card_progress": card_progress,
    }
    run["status"] = _trace_product_status(run, card_progress)
    gaps = _top_level_gaps(loaded, [key for key, _ in key_roots])
    if discovery["availability"] == "unavailable":
        gaps.append("discovery evidence unavailable")
    if card_build.get("availability"):
        gaps.append("card finalize evidence incomplete")
    tracking_tasks: list[TaskRow | TraceHead] = [head, *loaded["tasks"]]
    result: SourceRunTrace = {
        "source_run_id": _task_id(fetch_task_id),
        "events_archived": events_archived,
        "events_hot_window_days": hot_days,
        "run": run,
        "discovery": discovery,
        "card_build": card_build,
        "retired_steps": project_retired_steps(checkpoint, pipeline_rows),
        "items": items,
        "task_tracking": [
            _tracking_row(task_row)
            for task_row in tracking_tasks
            if task_row["task_type"] not in RETIRED_TASK_TYPES
        ],
        "acquisition": {
            "provider": _safe_label(checkpoint["provider"]),
            "connector": _identifier("connector", checkpoint["connector_id"]),
            "operation": _safe_label(checkpoint["operation"]),
            "invocation_evidence": "partial" if checkpoint["available"] else "unavailable",
            "aggregate": _aggregate(loaded),
            "cursor_before": _safe_cursor(checkpoint["cursor_before"]),
            "cursor_after": _safe_cursor(checkpoint["cursor_after"]),
            "checkpoint": {
                "evidence": "commit-associated keys" if checkpoint["available"] else "unavailable",
                "committed_at": loaded["committed_at"] or "unavailable",
                "commit_run_id": _identifier("run", loaded["run_id"]),
                "key_count": len(key_roots),
            },
        },
        "item_page": {
            "items": items,
            "item_total": len(key_roots),
            "truncated": has_more,
            "next_cursor": encode_item_cursor(next_index) if has_more else None,
        },
        "gaps": gaps,
    }
    return result


def _trace_product_status(run: SourceRunSummary, progress: CompleteCardProgress) -> str:
    """Expose the same user-facing state used by the Source Run list.

    ``execution_status`` remains the durable FETCH task fact.  The page status
    describes whether the whole collector output is ready, still active, or
    needs attention.
    """

    execution_status = run["execution_status"]
    if execution_status == "running":
        return "running"
    if execution_status == "pending":
        return "pending"
    if execution_status in {"failed", "blocked", "cancelled"}:
        return "attention"
    if run.get("completeness_status") == "partial" or (progress["partial"] or 0) or (progress["failed"] or 0):
        return "attention"
    total = progress["total"]
    if total and (progress["complete"] or 0) == total:
        return "completed"
    return "attention"


def encode_item_cursor(index: int) -> str:
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("invalid source-run item cursor index")
    payload = json.dumps(
        {"index": index, "v": 1},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_item_cursor(value: str) -> int:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        index = payload["index"]
        version = payload["v"]
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid source-run item cursor") from exc
    if set(payload) != {"index", "v"} or version != 1 or isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("invalid source-run item cursor")
    if encode_item_cursor(index) != value:
        raise ValueError("invalid source-run item cursor")
    return index


def project_source_run_summary(row: SourceRunSummaryRow) -> SourceRunSummary:
    artifacts = row["artifacts"]
    checkpoint = row["checkpoint"]
    entry_kind = row["entry_kind"]
    fetch_id = _task_id(row["task_id"])
    source_run_id = fetch_id if entry_kind == "run" else _task_id(row["stable_id"])
    return {
        **acquisition_artifacts(checkpoint["acquisition"] or artifacts["acquisition"]),
        "source_run_id": source_run_id,
        "fetch_task_id": fetch_id,
        "source_id": _identifier("source", row["target_id"] or checkpoint["source_id"]),
        "platform": _safe_label(row["platform"]),
        "source_type": _safe_label(row["source_type"]),
        "task_view": source_task_view(row["platform"] or "", row["source_type"] or ""),
        "source_display_name": _clip(row["display_name"]) or "unavailable",
        "trigger": _trigger(row, artifacts, checkpoint),
        "entry_kind": entry_kind,
        "scheduled_at": row["scheduled_at"],
        "trace_available": bool(row["trace_available"]),
        "execution_status": row["execution_status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "chain_id": _identifier("chain", artifacts["chain_id"]),
        "chain_root": _task_id(artifacts["chain_root_task_id"]),
        "batch_ids": [_identifier("batch", value) for value in row["batch_ids"]],
        "batch_ids_total": row["batch_ids_total"],
        "batch_ids_truncated": row["batch_ids_truncated"],
        "commit_run_id": _identifier("run", row["run_id"]),
        "commit_evidence": {
            "status": row["commit_status"] or "unavailable",
            "started_at": row["commit_started_at"] or "unavailable",
            "finished_at": row["commit_finished_at"] or "unavailable",
        },
        "aggregate": _aggregate(row),
        **tracking_fields(_tracking_input(row)),
    }


def _tracking_row(row: TaskRow | TraceHead) -> TrackingRow:
    return {
        "task_id": _task_id(row["task_id"]),
        "task_type": _safe_label(row["task_type"]),
        "status": _safe_label(row["status"]),
        **tracking_fields(_tracking_input(row)),
    }


def _trigger(row: SourceRunSummaryRow, artifacts: RunArtifacts, checkpoint: RunCheckpoint) -> str:
    if checkpoint["initial_fetch"]:
        return "initial"
    value = artifacts["trigger"] or artifacts["reason"] or row["trigger"]
    return _safe_label(value)


def _aggregate(row: SourceRunSummaryRow) -> Aggregate:
    checkpoint = row["checkpoint"]
    return {
        "fetched": run_count(row["fetched_count"], checkpoint["fetched_count"]),
        "visible": run_count(row["visible_count"], checkpoint["visible_count"]),
        "new": run_count(row["new_count"], checkpoint["fetched_count"]),
        "dedup_skipped": run_count(row["dedup_skipped_count"], checkpoint["dedup_skipped_count"]),
    }






def _checkpoint_key_roots(checkpoint: RunCheckpoint) -> list[tuple[tuple[str, str], str | None]]:
    roots = [str(value) for value in checkpoint["card_build_task_ids"]]
    keys = checkpoint_raw_keys(checkpoint["raw_item_keys"])
    if not keys:
        connector = checkpoint["connector_key"]
        keys = [(connector, str(raw_id)) for raw_id in checkpoint["raw_item_ids"] if connector and raw_id]
    root_by_key: dict[tuple[str, str], str | None] = {}
    for index, key in enumerate(keys):
        root_id = roots[index] if index < len(roots) and roots[index] else None
        root_by_key[key] = root_id if key not in root_by_key else None
    return sorted(root_by_key.items())


def _item_projection(
    base_dir: Path | str, loaded: TracePage, key: tuple[str, str], expected_root_id: str | None
) -> TraceItem:
    connector_id, raw_item_id = key
    raw = next((row for row in loaded["raw_items"] if (row["connector_id"], row["raw_item_id"]) == key), None)
    root = _item_root_task(loaded, key, expected_root_id)
    tasks = [task for task in loaded["tasks"] if root and task["root_id"] == root["task_id"]]
    card_id = next((task["artifacts"]["card_id"] for task in tasks if task["artifacts"]["card_id"]), None)
    card = next((row for row in loaded["cards"] if row["card_id"] == card_id), None)
    association = _association(root, loaded) if root else "provenance_unavailable"
    execution = _execution_for_association(root, loaded["events"], association)
    provenance = _card_provenance(root, association)
    return {
        **project_item_capture(loaded, raw=raw, card=card, tasks=tasks),
        "connector_id": _identifier("connector", connector_id),
        "raw_item_id": _identifier("raw", raw_item_id),
        "card_id": _identifier("card", card_id),
        "association": association,
        "execution": execution,
        "card_projection_provenance": provenance,
        "current_projection_url": _current_url(raw, card, key),
        "content_enrich": _content_enrich_evidence(tasks),
        "stages": [_stage(task, loaded, card_id) for task in tasks if task["task_type"] not in RETIRED_TASK_TYPES],
        "media_branches": project_media(base_dir, loaded, card_id),
        "final_card": project_final_card(base_dir, loaded, card, provenance),
    }


def _content_enrich_evidence(tasks: Sequence[TaskRow]) -> ContentEnrich:
    candidates = [task for task in tasks if task["task_type"] == "content_enrich"]
    if not candidates:
        return {
            "availability": "unavailable",
            "task_id": "unavailable",
            "status": "unavailable",
            "attempt": None,
            "max_attempts": None,
            "retryable": False,
            "error": None,
        }
    task = max(
        candidates,
        key=lambda item: (item["attempt"], item["task_id"]),
    )
    status = task["status"]
    attempt = task["attempt"]
    max_attempts = task["max_attempts"]
    return {
        "availability": "available",
        "task_id": _task_id(task["task_id"]),
        "status": status,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "retryable": status in {"failed", "blocked"} and attempt < max_attempts,
        "error": _safe_error(task),
    }


def _item_root_task(
    loaded: TracePage, key: tuple[str, str], expected_root_id: str | None
) -> TaskRow | None:
    if expected_root_id is None:
        return None
    candidates = [task for task in loaded["tasks"] if task["task_id"] == expected_root_id
                  and task["root_id"] == expected_root_id and task["task_type"] == "card_build"]
    for task in candidates:
        decoded = _decode_raw_target(task["target_id"])
        if decoded == key or (_legacy_root_matches(task, key, loaded["checkpoint"])):
            return task
    return None


def _legacy_root_matches(task: TaskRow, key: tuple[str, str], checkpoint: RunCheckpoint) -> bool:
    if _decode_raw_target(task["target_id"]) is not None or task["target_id"] != key[1]:
        return False
    connector = checkpoint["connector_id"]
    raw_ids = [str(value) for value in checkpoint["raw_item_ids"]]
    return bool(connector and key[0] == connector and raw_ids.count(key[1]) == 1)


def _decode_raw_target(value: str) -> tuple[str, str] | None:
    if not value.startswith(_RAW_PREFIX):
        return None
    try:
        encoded = value.removeprefix(_RAW_PREFIX)
        pair = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return (str(pair[0]), str(pair[1])) if isinstance(pair, list) and len(pair) == 2 else None


def _association(task: TaskRow, loaded: TracePage) -> str:
    if task["association_hint"] == "referenced_existing_pipeline":
        return "referenced_existing_pipeline"
    fetch_id = str(loaded["task_id"])
    if task["task_id"] == fetch_id:
        return "associated_with_this_run"
    if task["task_id"] in {row["task_id"] for row in loaded["external_tasks"]}:
        return "external_dependency"
    fetch_chain = loaded["artifacts"]["chain_id"]
    root_id = task["root_id"]
    root = next((row for row in loaded["tasks"] if row["task_id"] == root_id and row["root_id"] == root_id), task)
    root_chain = root["artifacts"]["chain_id"]
    if fetch_id in set(loaded["root_ancestors"].get(root_id, [])) and root_chain and root_chain == fetch_chain:
        return "associated_with_this_run"
    if root_id in set(loaded["checkpoint"]["card_build_task_ids"]):
        return "referenced_existing_pipeline"
    return "provenance_unavailable"


def _execution(task: TaskRow | None, events: Sequence[Mapping[str, object]]) -> str:
    if task is None:
        return "not_applicable"
    evidence = any(row["task_id"] == task["task_id"] and row["event_type"] in _EVENT_EVIDENCE for row in events)
    durable_columns = bool(task["claimed_by"] or task["started_at"] or task["finished_at"])
    if evidence or durable_columns:
        return "executed_in_this_run"
    return "not_started" if task["status"] == "pending" else "execution_evidence_unavailable"


def _execution_for_association(task: TaskRow | None, events: Sequence[Mapping[str, object]], association: str) -> str:
    if association in {"referenced_existing_pipeline", "external_dependency"}:
        return "not_applicable"
    if association == "provenance_unavailable":
        return "execution_evidence_unavailable"
    return _execution(task, events)


def _stage(task: TaskRow, loaded: TracePage, card_id: str | None) -> Stage:
    association = _association(task, loaded)
    deps: list[Dependency] = []
    by_id = {row["task_id"]: row for row in [*loaded["tasks"], *loaded["external_tasks"]]}
    for spec in task["depends"]:
        dependency_id = spec.get("task_id") if isinstance(spec, dict) else None
        dependency = by_id.get(dependency_id) if isinstance(dependency_id, str) else None
        deps.append({
            "task_id": _task_id(spec.get("task_id")) if isinstance(spec, dict) else "unavailable",
            "required_status": spec.get("required_status") if isinstance(spec, dict) else "unavailable",
            "actual_status": dependency["status"] if dependency else "unavailable",
            "satisfied": bool(dependency and isinstance(spec, dict) and dependency["status"] == spec.get("required_status")),
            "association": _association(dependency, loaded) if dependency else "provenance_unavailable",
        })
    return {
        "task_id": _task_id(task["task_id"]), "stage": task["task_type"], "status": task["status"],
        "operation": task["task_type"], "association": association,
        "execution": _execution_for_association(task, loaded["events"], association), "dependencies": deps,
        "input": {"target_kind": task["target_kind"], "target_id": _response_target_id(task),
                  "input_version": _identifier("version", task["input_version"])},
        "output": None, "product_gap": None,
        "attempt": task["attempt"], "max_attempts": task["max_attempts"],
        "retry_at": task["next_run_at"], "lease_expires_at": task["lease_expires_at"],
        "started_at": task["started_at"], "finished_at": task["finished_at"],
        "duration_seconds": _duration(task),
        "error": _safe_error(task),
        "versions": {"input": _identifier("version", task["input_version"]),
                     "result": _identifier("version", task["result_version"])},
        "downstream_task_ids": [_task_id(value) for value in sorted(_direct_children(loaded["tasks"], task["task_id"]))],
    }


def _card_provenance(root: TaskRow | None, association: str) -> str:
    if root and association == "associated_with_this_run" and root["status"] == "success" and root["artifacts"]["created_or_updated"] in {"created", "updated"}:
        return "created_or_updated_by_this_pipeline"
    if association == "referenced_existing_pipeline":
        return "reused_existing_pipeline"
    return "provenance_unavailable"


def _current_url(
    raw: RawItemRow | None, card: TraceCardRow | None, business_key: tuple[str, str]
) -> str:
    for value in (raw["source_url"] if raw else None, card["source_url"] if card else None):
        safe = _safe_remote(value)
        leaks_unsafe_key = any(
            part and safe_identifier(part) == "unavailable" and part in str(value)
            for part in business_key
        )
        if safe and not leaks_unsafe_key:
            return safe
    return "unavailable"


def _safe_remote(value: object) -> str | None:
    return sanitize_external_url(value)


def _safe_cursor(value: object) -> str:
    if value is None:
        return "unavailable"
    return redact_text(value, limit=500)


def _safe_label(value: object) -> str:
    return safe_label(value)


def _task_id(value: object) -> str:
    return task_identifier_alias(value)


def _identifier(kind: str, value: object) -> str:
    return safe_or_opaque_identifier(kind, value)


def _response_target_id(task: TaskRow) -> str:
    value = task["target_id"]
    if value.startswith(_RAW_PREFIX):
        _decode_raw_target(value)
        return opaque_identifier("target", value)
    kind = task["target_kind"]
    return _identifier(kind if kind in {"card", "source", "asset", "decision"} else "target", value)


def _safe_error(task: TaskRow | TraceHead) -> SafeError | None:
    if not task["error_reason"] and not task["last_failure_class"]:
        return None
    category = safe_label(task["last_failure_class"] or "task_error", limit=80)
    return {"class": category, "summary": redact_text(task["error_reason"] or category, limit=_ERROR_LIMIT),
            "next_action": "retry" if task["attempt"] < task["max_attempts"] else "inspect configuration"}


def _duration(task: TaskRow | TraceHead) -> int | None:
    if not task["started_at"] or not task["finished_at"]:
        return None
    from datetime import datetime
    try:
        start = datetime.fromisoformat(task["started_at"].replace("Z", "+00:00"))
        finish = datetime.fromisoformat(task["finished_at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((finish - start).total_seconds()))


def _direct_children(tasks: Sequence[TaskRow], task_id: str) -> list[str]:
    return [task["task_id"] for task in tasks if any(isinstance(dep, dict) and dep.get("task_id") == task_id for dep in task["depends"])]


def _top_level_gaps(loaded: TracePage, keys: list[tuple[str, str]]) -> list[str]:
    gaps = []
    if not loaded["checkpoint"]["available"]:
        gaps.append("acquisition checkpoint unavailable")
    if keys and len(loaded["raw_items"]) < len(keys):
        gaps.append("one or more current RawItem projections unavailable")
    if not loaded["run_id"]:
        gaps.append("commit run history unavailable")
    if loaded["graph_gaps"]:
        gaps.append("pipeline graph truncated at configured safety limit")
    return gaps


def _clip(value: object, limit: int = _TEXT_LIMIT) -> str:
    return redact_text(value, limit=limit)



def _tracking_input(row: SourceRunSummaryRow | TaskRow) -> TrackingInput:
    return {
        "status": row["status"], "created_at": row["created_at"],
        "started_at": row["started_at"], "finished_at": row["finished_at"],
        "updated_at": row["updated_at"], "queue_name": row["queue_name"],
        "priority": row["priority"], "deadline_at": row["deadline_at"],
        "claimed_by": row["claimed_by"],
        "acquisition": row["tracking_acquisition"],
    }
