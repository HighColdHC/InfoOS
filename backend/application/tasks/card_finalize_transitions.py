"""Terminal scheduling rules for the collector's information-card product."""

from __future__ import annotations


from pathlib import Path
from typing import Any

from backend.application.tasks.input_versions import card_finalize_input_version
from backend.domain.media_outcomes import is_resolved_x_no_media_outcome
from backend.application.tasks.planner import card_task_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.tasks import is_task_cancelled


TERMINAL_STATUSES = {
    TaskStatus.SUCCESS,
    TaskStatus.FAILED,
    TaskStatus.BLOCKED,
    TaskStatus.SKIPPED,
}


def enqueue_card_finalize_after_media(
    repo: TaskRepository,
    task: Task,
    artifacts: dict[str, Any],
    base_dir: Path,
    status: TaskStatus,
) -> list[str]:
    """Finalize even when media acquisition is terminal with honest gaps."""
    card = _durable_card(base_dir, task.target_id) or {}
    resolved_x_no_media = is_resolved_x_no_media_outcome(
        source_platform=card.get("source_platform"),
        download_status=artifacts.get("download_status"),
        candidate_count=artifacts.get("candidate_count"),
        reasons=artifacts.get("gap_reasons") or (),
    )
    return enqueue_card_finalize(
        repo,
        task.target_id,
        dependency_task_id=task.task_id,
        parent_task=task,
        base_dir=base_dir,
        media_gaps=[]
        if artifacts.get("download_status") == "skipped_no_media_expected" or resolved_x_no_media
        else [_media_gap(task, artifacts, status)],
    )


def enqueue_card_finalize_after_pipeline_failure(
    repo: TaskRepository,
    task: Task,
    artifacts: dict[str, Any],
    base_dir: Path,
    status: TaskStatus,
) -> list[str]:
    """Publish readable source facts even when optional content enrichment stops."""
    reason = str(
        artifacts.get("reason")
        or artifacts.get("blocked_reason")
        or task.error_reason
        or status.value
    )
    return enqueue_card_finalize(
        repo,
        task.target_id,
        dependency_task_id=task.task_id,
        parent_task=task,
        base_dir=base_dir,
        media_gaps=[
            {
                "missing_part": "content_enrichment",
                "reason": reason,
                "status": status.value,
                "task_id": task.task_id,
            }
        ],
    )


def enqueue_finalize_if_media_terminal(
    repo: TaskRepository,
    task: Task,
    artifacts: dict[str, Any],
    base_dir: Path,
    *,
    required_status: TaskStatus,
    force_gap: bool = False,
) -> list[str]:
    card_id = str(artifacts.get("card_id") or task.artifacts_summary.get("card_id") or "")
    if not card_id:
        asset = _durable_asset(base_dir, task.target_id)
        card_id = asset["card_id"] if asset else ""
    if not card_id:
        return []
    transcript_not_applicable = bool(artifacts.get("transcript_not_applicable"))
    transcript_satisfied = bool(artifacts.get("transcript_satisfied"))
    gaps = [] if (
        (required_status == TaskStatus.SUCCESS and not force_gap)
        or transcript_not_applicable
        or transcript_satisfied
        or bool(artifacts.get("derivation_satisfied"))
    ) else [_media_gap(task, artifacts, required_status)]
    return enqueue_card_finalize(
        repo,
        card_id,
        dependency_task_id=task.task_id,
        parent_task=task,
        base_dir=base_dir,
        media_gaps=gaps,
    )


def enqueue_card_finalize(
    repo: TaskRepository,
    card_id: str,
    *,
    dependency_task_id: str,
    parent_task: Task | None,
    base_dir: Path,
    media_gaps: list[dict[str, str]] | None = None,
) -> list[str]:
    from backend.infrastructure.storage.repositories import (
        CardAssetRepository,
        CardContentBlockRepository,
    )

    card = _durable_card(base_dir, card_id)
    if card is None:
        return []
    upstream_tasks = _finalize_media_tasks(
        card_id,
        repo,
        base_dir,
        chain_id=str(parent_task.artifacts_summary.get("chain_id") or "") if parent_task else "",
        fallback_task_id=dependency_task_id,
    )
    if upstream_tasks is None:
        return []
    blocks = CardContentBlockRepository(base_dir).list_for_card(card_id)
    assets = CardAssetRepository(base_dir).list_for_card(card_id)
    input_version = card_finalize_input_version(card, blocks, assets)
    inherited_gaps = list(parent_task.artifacts_summary.get("media_gaps") or []) if parent_task else []
    upstream_media_tasks = sorted(
        ({"task_id": upstream.task_id, "status": upstream.status.value} for upstream in upstream_tasks),
        key=lambda evidence: evidence["task_id"],
    )
    finalize_artifacts = {
        "card_id": card_id,
        "media_gaps": [*inherited_gaps, *(media_gaps or [])],
        "upstream_media_tasks": upstream_media_tasks,
    }
    child = Task(
        task_id=card_task_id(TaskType.CARD_FINALIZE, card_id, input_version=input_version),
        task_type=TaskType.CARD_FINALIZE,
        target_kind="card",
        target_id=card_id,
        depends_on=(),
        input_version=input_version,
        artifacts_summary=_child_task_artifacts(
            parent_task,
            finalize_artifacts,
            upstream_task_id=dependency_task_id,
        ),
    )
    repo.enqueue(child)
    return [child.task_id]


def _finalize_media_tasks(
    card_id: str,
    repo: TaskRepository,
    base_dir: Path,
    *,
    chain_id: str,
    fallback_task_id: str,
) -> list[Task] | None:
    from backend.infrastructure.storage.repositories import CardAssetRepository

    fallback = repo.get(fallback_task_id)
    if (
        fallback is not None
        and fallback.status in TERMINAL_STATUSES
        and fallback.artifacts_summary.get("download_status") == "skipped_transcript_available"
    ):
        return [fallback]

    def in_chain(candidate: Task) -> bool:
        return not chain_id or str(candidate.artifacts_summary.get("chain_id") or "") == chain_id

    tasks: list[Task] = []
    for asset in CardAssetRepository(base_dir).list_for_card(card_id):
        asset_id = asset["asset_id"]
        asset_kind = asset["asset_kind"]
        candidates: list[Task] = []
        if asset_kind == "video":
            extracts = [
                task
                for task in repo.list_by_target(
                    target_kind="asset",
                    target_id=asset_id,
                    task_type=TaskType.VIDEO_EXTRACT_AUDIO,
                )
                if in_chain(task)
            ]
            if extracts:
                extract = extracts[-1]
                transcribes = [
                    task
                    for task in repo.list_by_target(
                        target_kind="asset",
                        target_id=asset_id,
                        task_type=TaskType.VIDEO_TRANSCRIBE,
                    )
                    if in_chain(task)
                    and any(dependency.task_id == extract.task_id for dependency in task.depends_on)
                ]
                candidates = transcribes or [extract]
        candidates = [candidate for candidate in candidates if in_chain(candidate)]
        if not candidates:
            # Video transcription is scoped to the
            # current acquisition chain. A previously downloaded, deliverable
            # asset must not keep a later card version in processing merely
            # because that chain did not schedule the optional derivative.
            if asset["status"] in {
                "available", "done", "downloaded", "enriched", "preserved",
                "ready", "success", "understood", "failed", "skipped",
            }:
                continue
            return None
        current = candidates[-1]
        if current.status not in TERMINAL_STATUSES or is_task_cancelled(current):
            return None
        tasks.append(current)

    if not tasks:
        if fallback is None or fallback.status not in TERMINAL_STATUSES:
            return None
        tasks.append(fallback)
    return tasks


def _child_task_artifacts(
    parent_task: Task | None,
    extra: dict[str, Any],
    *,
    upstream_task_id: str,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    if parent_task is not None:
        for key in ("chain_id", "chain_root_task_id", "capture_id", "initial_fetch"):
            value = parent_task.artifacts_summary.get(key)
            if value:
                artifacts[key] = value
    artifacts["upstream_task_id"] = upstream_task_id
    artifacts.update(extra)
    return artifacts


def _media_gap(task: Task, artifacts: dict[str, Any], status: TaskStatus) -> dict[str, str]:
    missing_part = {
        TaskType.MEDIA_DOWNLOAD: "media_download",
        TaskType.VIDEO_EXTRACT_AUDIO: "audio_extract",
        TaskType.VIDEO_TRANSCRIBE: "video_transcript",
    }.get(task.task_type, "media")
    reasons = artifacts.get("gap_reasons") or []
    reason = str(reasons[0]) if reasons else str(
        artifacts.get("reason") or artifacts.get("blocked_reason") or status.value
    )
    return {
        "missing_part": missing_part,
        "reason": reason,
        "status": status.value,
        "task_id": task.task_id,
    }


def _durable_card(base_dir: Path, card_id: str) -> dict[str, Any] | None:
    from backend.infrastructure.storage.repositories import CardRepository

    return CardRepository(base_dir).get_card(card_id) if card_id else None


def _durable_asset(base_dir: Path, asset_id: str) -> dict[str, Any] | None:
    from backend.infrastructure.storage.repositories import CardAssetRepository

    return CardAssetRepository(base_dir).get_asset(asset_id) if asset_id else None
