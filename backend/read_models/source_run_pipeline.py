"""Bounded list projection for Source Run pipeline health and durable issues."""
from __future__ import annotations

from backend.domain.timestamps import parse_utc_or_none

from datetime import datetime, timezone
from typing import Literal, TypedDict
from collections.abc import Sequence

from backend.domain.json_types import JsonObject
from backend.infrastructure.storage.source_run_json import RunArtifacts, RunCheckpoint, parse_run_assets
from backend.infrastructure.storage.source_run_trace_queries_types import AssetStatusRow, GraphTaskRow, PipelineRows, SourceRunSummaryRow

from backend.domain.tasks import RETIRED_TASK_TYPES, TaskType
from backend.infrastructure.storage.source_run_trace_queries import GRAPH_DEPTH_LIMIT, GRAPH_NODE_LIMIT


class TaskCounts(TypedDict):
    total: int
    success: int
    failed: int


class CardProgress(TypedDict):
    total: int
    complete: int
    partial: int
    failed: int


class AcquisitionStage(TypedDict):
    command_executions: int
    command_successes: int
    pending_enrichment_links: int


class MediaCounts(TypedDict):
    image_download: TaskCounts
    video_download: TaskCounts


class EnrichmentStage(MediaCounts):
    video_transcription: TaskCounts
    card_pack: TaskCounts


class TrackingProjection(TypedDict):
    acquisition_stage: AcquisitionStage
    enrichment_stage: EnrichmentStage
    card_progress: CardProgress


class PipelineBucket(TypedDict):
    bucket: str
    counts: dict[str, int]
    health: str


class Pipeline(TypedDict):
    evidence: str
    projection_truncated: bool
    referenced_item_count: int
    provenance_unavailable_count: int
    buckets: list[PipelineBucket]


class PipelineIssue(TypedDict):
    code: str
    severity: str
    count: int
    evidence: str


class PipelineProjection(TrackingProjection):
    pipeline: Pipeline
    issues: list[PipelineIssue]
    blocking_reasons: list[str]


class RetiredSteps(TypedDict):
    count: int
    availability: str


class PipelineTask(TypedDict):
    task_id: str
    task_type: str
    status: str
    lease_expires_at: str | None
    next_run_at: str | None
    last_failure_class: str | None
    artifacts: RunArtifacts


BUCKET_ORDER = (
    "fetch", "card_build", "content_enrich", "media", "card_finalize",
)
TASK_BUCKET = {
    TaskType.FETCH.value: "fetch",
    TaskType.CARD_BUILD.value: "card_build",
    TaskType.CONTENT_ENRICH.value: "content_enrich",
    TaskType.MEDIA_DOWNLOAD.value: "media",
    TaskType.VIDEO_EXTRACT_AUDIO.value: "media",
    TaskType.VIDEO_TRANSCRIBE.value: "media",
    TaskType.CARD_FINALIZE.value: "card_finalize",
}
STATUSES = ("pending", "running", "success", "failed", "blocked", "skipped")
ISSUE_META = {
    "task_failed": ("error", "durable_task_state"),
    "task_blocked": ("warning", "durable_task_state"),
    "expired_lease": ("error", "task_lease"),
    "retry_scheduled": ("info", "task_schedule"),
    "checkpoint_unavailable": ("warning", "acquisition_checkpoint"),
    "commit_history_unavailable": ("warning", "commit_history"),
    "lineage_evidence_unavailable": ("warning", "task_lineage"),
}
_ACTIONABLE_BLOCKING_REASONS = {
    "source_unavailable",
    "platform_not_logged_in",
    "platform_challenge_required",
    "browser_session_not_ready",
    "browser_page_load_failed",
    "chrome_not_configured",
    "opencli_daemon_down",
    "browser_session_render_blocked",
}


def project_list_pipelines(
    fetch_rows: Sequence[SourceRunSummaryRow], loaded: PipelineRows, *, evaluated_at: str | None = None
) -> dict[str, PipelineProjection]:
    now = parse_utc_or_none(evaluated_at) or datetime.now(timezone.utc)
    graph_by_root: dict[str, list[GraphTaskRow]] = {}
    truncated_roots: set[str] = set(loaded["ancestor_gaps"])
    for raw in loaded["graph"]:
        row = raw
        root_id = row["root_id"]
        if row["graph_rank"] > GRAPH_NODE_LIMIT or row["graph_depth"] > GRAPH_DEPTH_LIMIT:
            truncated_roots.add(root_id)
            continue
        graph_by_root.setdefault(root_id, []).append(row)
    result = {}
    for fetch in fetch_rows:
        result[str(fetch["task_id"])] = _project_one(
            fetch, graph_by_root, truncated_roots, loaded, now=now
        )
    return result


def _project_one(
    fetch: SourceRunSummaryRow,
    graph_by_root: dict[str, list[GraphTaskRow]],
    truncated_roots: set[str],
    loaded: PipelineRows,
    *,
    now: datetime,
) -> PipelineProjection:
    fetch_id = str(fetch["task_id"])
    checkpoint = fetch["checkpoint"]
    roots = [str(value) for value in checkpoint["card_build_task_ids"] if value]
    fetch_chain = fetch["artifacts"]["chain_key"]
    associated_tasks = [_fetch_task(fetch)]
    referenced = 0
    omitted = loaded["omitted_by_fetch"][fetch_id]
    unavailable = 0
    truncated = bool(loaded["page_truncated"] and roots) or omitted > 0
    selected_count = max(0, len(roots) - omitted)
    for root_id in roots[:selected_count]:
        tasks = graph_by_root.get(root_id)
        if not tasks:
            unavailable += 1
            continue
        root = next((task for task in tasks if task["task_id"] == root_id), None)
        if root is None:
            unavailable += 1
            continue
        ancestors = set(loaded["ancestors"].get(root_id, []))
        root_chain = root["artifacts"]["chain_key"]
        if fetch_id in ancestors and root_chain and root_chain == fetch_chain:
            associated_tasks.extend(tasks)
        else:
            referenced += 1
        if root_id in truncated_roots:
            truncated = True
    checkpoint_available = fetch["checkpoint"]["available"]
    associated_tasks = [
        task for task in associated_tasks
        if TASK_BUCKET.get(task["task_type"]) is not None
    ]
    associated_tasks = list({task["task_id"]: task for task in associated_tasks}.values())
    commit_available = bool(fetch["commit_status"])
    evidence = "unavailable" if not checkpoint_available else (
        "partial" if truncated or unavailable or not commit_available else "complete"
    )
    buckets = _buckets(associated_tasks, evidence=evidence)
    issue_counts = _task_issue_counts(associated_tasks, now=now)
    if not checkpoint_available:
        issue_counts["checkpoint_unavailable"] = 1
    if not commit_available:
        issue_counts["commit_history_unavailable"] = 1
    if unavailable:
        issue_counts["lineage_evidence_unavailable"] = unavailable
    return {
        "pipeline": {
            "evidence": evidence,
            "projection_truncated": truncated,
            "referenced_item_count": referenced,
            "provenance_unavailable_count": unavailable + omitted,
            "buckets": buckets,
        },
        "issues": _issues(issue_counts),
        "blocking_reasons": _blocking_reasons(associated_tasks),
        **_tracking_projection(fetch, associated_tasks, loaded["assets"]),
    }


def project_planned_source_run() -> PipelineProjection:
    """Return the existing list shape plus zero-valued tracking for a future occurrence."""
    counts = {status: 0 for status in STATUSES} | {"total": 0}
    return {
        "pipeline": {
            "evidence": "unavailable",
            "projection_truncated": False,
            "referenced_item_count": 0,
            "provenance_unavailable_count": 0,
            "buckets": [
                {"bucket": bucket, "counts": dict(counts), "health": "unavailable"}
                for bucket in BUCKET_ORDER
            ],
        },
        "issues": [],
        "blocking_reasons": [],
        **_zero_tracking_projection(),
    }


def _tracking_projection(
    fetch: SourceRunSummaryRow, tasks: Sequence[PipelineTask], assets: list[AssetStatusRow]
) -> TrackingProjection:
    return {
        "acquisition_stage": {
            "command_executions": _command_executions(fetch),
            "command_successes": int(fetch["status"] == "success"),
            "pending_enrichment_links": sum(
                task["task_type"] == TaskType.CONTENT_ENRICH.value
                and task["status"] == "pending"
                for task in tasks
            ),
        },
        "enrichment_stage": {
            **_media_download_counts(tasks, assets),
            "video_transcription": _task_counts(tasks, TaskType.VIDEO_TRANSCRIBE.value),
            "card_pack": _task_counts(tasks, TaskType.CARD_FINALIZE.value),
        },
        "card_progress": _card_progress(tasks),
    }


def _command_executions(fetch: SourceRunSummaryRow) -> int:
    """Count completed attempts from durable events, with a legacy-row fallback."""
    completed_attempts = fetch["execution_event_count"]
    if completed_attempts:
        return completed_attempts + int(fetch["status"] == "running")

    status = fetch["status"]
    attempt = fetch["attempt"]
    never_started_pending = (
        status == "pending"
        and attempt <= 1
        and not any(fetch[key] for key in ("claimed_by", "started_at", "finished_at"))
    )
    if never_started_pending:
        return 0
    if status == "pending":
        return max(0, attempt - 1)
    if status in {"failed", "blocked", "skipped"} and fetch["last_failure_class"]:
        return max(0, attempt - 1)
    return attempt


def _zero_tracking_projection() -> TrackingProjection:
    zero: TaskCounts = {"total": 0, "success": 0, "failed": 0}
    return {
        "acquisition_stage": {
            "command_executions": 0,
            "command_successes": 0,
            "pending_enrichment_links": 0,
        },
        "enrichment_stage": {
            "image_download": zero.copy(),
            "video_download": zero.copy(),
            "video_transcription": zero.copy(),
            "card_pack": zero.copy(),
        },
        "card_progress": {"total": 0, "complete": 0, "partial": 0, "failed": 0},
    }


def _card_progress(tasks: Sequence[PipelineTask]) -> CardProgress:
    finalize = [task for task in tasks if task["task_type"] == TaskType.CARD_FINALIZE.value]
    return {
        "total": len(finalize),
        "complete": sum(
            task["status"] == "success"
            and task["artifacts"]["completeness_status"] == "complete"
            for task in finalize
        ),
        "partial": sum(
            task["status"] == "success"
            and task["artifacts"]["completeness_status"] == "partial"
            for task in finalize
        ),
        "failed": sum(task["status"] in {"failed", "blocked"} for task in finalize),
    }


def _task_counts(tasks: Sequence[PipelineTask], task_type: str) -> TaskCounts:
    selected = [task for task in tasks if task["task_type"] == task_type]
    return {
        "total": len(selected),
        "success": sum(task["status"] == "success" for task in selected),
        "failed": sum(task["status"] in {"failed", "blocked", "skipped"} for task in selected),
    }


def project_retired_steps(checkpoint: RunCheckpoint, loaded: PipelineRows) -> RetiredSteps:
    """Count unique historical steps in the bounded run graph without exposing tasks."""
    graph = loaded["graph"]
    retained = [row for row in graph if row["graph_rank"] <= GRAPH_NODE_LIMIT
                and row["graph_depth"] <= GRAPH_DEPTH_LIMIT]
    roots = set(checkpoint["card_build_task_ids"])
    present_roots = {row["task_id"] for row in retained}
    partial = (loaded["page_truncated"] or bool(loaded["ancestor_gaps"])
               or any(loaded["omitted_by_fetch"].values())
               or len(retained) != len(graph) or not roots.issubset(present_roots))
    return {
        "count": len({row["task_id"] for row in retained if row["task_type"] in RETIRED_TASK_TYPES}),
        "availability": "unavailable" if not checkpoint["available"] else "partial" if partial else "available",
    }


def _media_download_counts(
    tasks: Sequence[PipelineTask], assets: list[AssetStatusRow]
) -> MediaCounts:
    durable = {asset["asset_id"]: asset for asset in assets}
    selected: dict[str, JsonObject] = {}
    for task in tasks:
        if task["task_type"] != TaskType.MEDIA_DOWNLOAD.value:
            continue
        for asset_artifact in parse_run_assets(task["artifacts"]["assets"]):
            index, artifact = asset_artifact["index"], asset_artifact["payload"]
            asset_id = str(artifact.get("asset_id") or "")
            key = asset_id or f"{task['task_id']}:{index}"
            selected[key] = dict(durable[asset_id]) | artifact if asset_id in durable else artifact
    result: MediaCounts = {"image_download": TaskCounts(total=0, success=0, failed=0), "video_download": TaskCounts(total=0, success=0, failed=0)}
    branches: tuple[tuple[str, Literal["image_download", "video_download"]], ...] = (("image", "image_download"), ("video", "video_download"))
    for asset_kind, field in branches:
        matching = [asset for asset in selected.values() if asset.get("asset_kind") == asset_kind]
        result[field] = {
            "total": len(matching),
            "success": sum(asset.get("status") in {"downloaded", "understood"} for asset in matching),
            "failed": sum(asset.get("status") in {"failed", "blocked", "skipped"} for asset in matching),
        }
    return result


def _fetch_task(fetch: SourceRunSummaryRow) -> PipelineTask:
    return {
        "task_id": str(fetch["task_id"]), "status": fetch["status"],
        "lease_expires_at": fetch["lease_expires_at"], "next_run_at": fetch["next_run_at"],
        "last_failure_class": fetch["last_failure_class"], "task_type": "fetch",
        "artifacts": fetch["artifacts"],
    }


def _buckets(tasks: Sequence[PipelineTask], *, evidence: str) -> list[PipelineBucket]:
    result: list[PipelineBucket] = []
    for bucket in BUCKET_ORDER:
        selected = [task for task in tasks if TASK_BUCKET.get(task["task_type"]) == bucket]
        counts = {status: sum(task["status"] == status for task in selected) for status in STATUSES}
        counts["total"] = len(selected)
        result.append({"bucket": bucket, "counts": counts, "health": _health(counts, evidence=evidence)})
    return result


def _health(counts: dict[str, int], *, evidence: str) -> str:
    if counts["failed"] or counts["blocked"]:
        return "attention"
    if counts["running"]:
        return "running"
    if counts["pending"]:
        return "pending"
    if evidence != "complete":
        return "unavailable"
    if counts["total"] == 0:
        return "not_present"
    return "succeeded"


def _task_issue_counts(tasks: Sequence[PipelineTask], *, now: datetime) -> dict[str, int]:
    counts = {
        "task_failed": sum(task["status"] == "failed" for task in tasks),
        "task_blocked": sum(task["status"] == "blocked" for task in tasks),
        "expired_lease": sum(
            task["status"] == "running" and (lease := parse_utc_or_none(task["lease_expires_at"])) is not None and lease < now
            for task in tasks
        ),
        "retry_scheduled": sum(task["status"] == "pending" and bool(task["next_run_at"]) for task in tasks),
    }
    return {code: count for code, count in counts.items() if count}


def _blocking_reasons(tasks: Sequence[PipelineTask]) -> list[str]:
    """Project only stable, user-actionable classes from blocked durable tasks."""
    return sorted({
        reason
        for task in tasks
        if task["status"] == "blocked"
        and (reason := task["last_failure_class"])
        in _ACTIONABLE_BLOCKING_REASONS
    })


def _issues(counts: dict[str, int]) -> list[PipelineIssue]:
    return [
        {"code": code, "severity": ISSUE_META[code][0], "count": counts[code], "evidence": ISSUE_META[code][1]}
        for code in ISSUE_META if counts.get(code)
    ]
