from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.artifact_state import merged_task_artifacts as merged_task_artifacts
from backend.application.enrichment.external_doc_inputs import content_enrich_queue_artifacts
from backend.application.tasks.batches import attach_task_to_parent_batches
from backend.application.tasks.finalization import TaskFinalizationQueue
from backend.application.tasks.card_finalize_transitions import (
    enqueue_card_finalize,
    enqueue_card_finalize_after_pipeline_failure,
    enqueue_card_finalize_after_media,
    enqueue_finalize_if_media_terminal,
)
from backend.application.tasks.input_versions import (
    asset_input_version,
    enrichment_input_version,
    media_input_version,
    video_transcript_input_version,
)
from backend.application.tasks.planner import asset_task_id, card_task_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import SourceRepository
# BLOCKED is terminal work: it does not pretend to be enrichment, but it can
# explicitly contribute a gap to a partial context product.
TERMINAL_STATUSES = {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.SKIPPED}

CARD_PIPELINE_STAGES = (
    TaskType.FETCH,
    TaskType.CARD_BUILD,
    TaskType.CONTENT_ENRICH,
    TaskType.MEDIA_DOWNLOAD,
    TaskType.VIDEO_EXTRACT_AUDIO,
    TaskType.VIDEO_TRANSCRIBE,
    TaskType.CARD_FINALIZE,
)


def child_task_artifacts(
    parent_task: Task,
    extra: dict[str, Any] | None = None,
    *,
    upstream_task_id: str | None = None,
) -> dict[str, Any]:
    parent_artifacts = parent_task.artifacts_summary
    artifacts: dict[str, Any] = {}
    for key in ("chain_id", "chain_root_task_id", "capture_id", "initial_fetch"):
        value = parent_artifacts.get(key)
        if value is not None and value is not False:
            artifacts[key] = value
    artifacts["upstream_task_id"] = upstream_task_id or parent_task.task_id
    for key, value in (extra or {}).items():
        if value is not None:
            artifacts[key] = value
    return artifacts


def enqueue_downstream(
    repo: TaskFinalizationQueue,
    task: Task,
    *,
    status: TaskStatus,
    artifacts: dict[str, Any],
    base_dir: Path,
) -> list[str]:
    """Advance a card only from the product durably written by this stage."""
    if status not in TERMINAL_STATUSES:
        return []

    if task.task_type == TaskType.FETCH and status == TaskStatus.SUCCESS:
        child_ids = enqueue_card_tasks_after_fetch(repo, artifacts)
    elif task.task_type == TaskType.CARD_BUILD and status == TaskStatus.SUCCESS:
        child_ids = enqueue_content_enrich(repo, task, artifacts, base_dir)
    elif task.task_type == TaskType.CONTENT_ENRICH and status == TaskStatus.SUCCESS:
        if task.artifacts_summary.get("feishu_backfill"):
            child_ids = enqueue_card_finalize(
                repo, task.target_id, dependency_task_id=task.task_id,
                parent_task=task, base_dir=base_dir,
            )
        else:
            child_ids = enqueue_media_download(repo, task, base_dir)
    elif task.task_type == TaskType.CONTENT_ENRICH:
        child_ids = enqueue_card_finalize_after_pipeline_failure(
            repo, task, artifacts, base_dir, status
        )
    elif task.task_type == TaskType.MEDIA_DOWNLOAD and status == TaskStatus.SUCCESS:
        child_ids = enqueue_media_subtasks(repo, task, artifacts, base_dir)
    elif task.task_type == TaskType.MEDIA_DOWNLOAD:
        child_ids = enqueue_card_finalize_after_media(repo, task, artifacts, base_dir, status)
    elif task.task_type == TaskType.VIDEO_EXTRACT_AUDIO and status == TaskStatus.SUCCESS:
        child_ids = enqueue_video_transcribe(repo, task, artifacts, base_dir)
        if not child_ids and not artifacts.get("audio_path"):
            child_ids = enqueue_finalize_if_media_terminal(
                repo,
                task,
                {**artifacts, "reason": "missing_audio_path"},
                base_dir,
                required_status=status,
                force_gap=True,
            )
    elif task.task_type in {
        TaskType.VIDEO_EXTRACT_AUDIO,
        TaskType.VIDEO_TRANSCRIBE,
    }:
        child_ids = enqueue_finalize_if_media_terminal(repo, task, artifacts, base_dir, required_status=status)
    elif task.task_type == TaskType.CARD_FINALIZE and status == TaskStatus.SUCCESS:
        from backend.application.tasks.card_delivery_transitions import enqueue_after_card_finalize

        child_ids = enqueue_after_card_finalize(repo, task, artifacts)
    else:
        return []
    return _attach_durable_children_to_parent_batch(repo, task.task_id, child_ids, base_dir)


def _attach_durable_children_to_parent_batch(
    repo: TaskFinalizationQueue,
    parent_task_id: str,
    child_task_ids: list[str],
    base_dir: Path,
) -> list[str]:
    for child_task_id in child_task_ids:
        if repo.get(child_task_id) is None:
            raise RuntimeError(f"downstream_task_not_durable:{child_task_id}")
    attach_task_to_parent_batches(
        base_dir,
        parent_task_id=parent_task_id,
        child_task_ids=child_task_ids,
        conn=repo.connection,
    )
    return child_task_ids


def enqueue_card_tasks_after_fetch(repo: TaskRepository, artifacts: dict[str, Any]) -> list[str]:
    """FETCH owns raw acquisition only; its durable CARD_BUILD rows are children."""
    checkpoint = artifacts.get("acquisition_checkpoint")
    durable_artifacts = checkpoint if isinstance(checkpoint, dict) else artifacts
    requested_ids = [str(task_id) for task_id in durable_artifacts.get("card_build_task_ids") or []]
    return [task_id for task_id in requested_ids if repo.get(task_id) is not None]


def enqueue_content_enrich(
    repo: TaskRepository,
    task: Task,
    artifacts: dict[str, Any],
    base_dir: Path,
) -> list[str]:
    card_id = str(artifacts.get("card_id") or "")
    card = _durable_card(base_dir, card_id)
    if card is None:
        return []
    source = SourceRepository(base_dir).get_source(str(card.get("source_id") or ""))
    input_version = enrichment_input_version(card, source=source)
    child = Task(
        task_id=card_task_id(TaskType.CONTENT_ENRICH, card_id, input_version=input_version),
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id=card_id,
        depends_on=(DependencySpec(task_id=task.task_id, required_status=TaskStatus.SUCCESS),),
        input_version=input_version,
        artifacts_summary=child_task_artifacts(task, {
            "card_id": card_id, **content_enrich_queue_artifacts(card, base_dir),
        }),
    )
    repo.enqueue(child)
    return [child.task_id]
def enqueue_media_download(repo: TaskRepository, task: Task, base_dir: Path) -> list[str]:
    card = _durable_card(base_dir, task.target_id)
    if card is None:
        return []
    input_version = media_input_version(card=card, enrichment_version=task.input_version)
    child = Task(
        task_id=card_task_id(TaskType.MEDIA_DOWNLOAD, task.target_id, input_version=input_version),
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id=task.target_id,
        depends_on=(DependencySpec(task_id=task.task_id, required_status=TaskStatus.SUCCESS),),
        input_version=input_version,
        artifacts_summary=child_task_artifacts(task, {"card_id": task.target_id}),
    )
    repo.enqueue(child)
    return [child.task_id]


def enqueue_media_subtasks(
    repo: TaskRepository,
    task: Task,
    artifacts: dict[str, Any],
    base_dir: Path,
) -> list[str]:
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository
    from backend.application.tasks.media_outputs import media_output_present

    requested_asset_ids = {str(asset.get("asset_id") or "") for asset in artifacts.get("assets") or []}
    assets = CardAssetRepository(base_dir).list_for_card(task.target_id)
    blocks = CardContentBlockRepository(base_dir).list_for_card(task.target_id)
    if artifacts.get("download_status") == "skipped_transcript_available": assets = []
    if requested_asset_ids:
        assets = [asset for asset in assets if asset["asset_id"] in requested_asset_ids]
    created: list[str] = []
    for asset in assets:
        if media_output_present(asset, blocks):
            continue
        asset_id = asset["asset_id"]
        if asset["asset_kind"] == "video":
            next_type = TaskType.VIDEO_EXTRACT_AUDIO
        else:
            continue
        input_version = asset_input_version(asset=asset, media_version=task.input_version, stage=next_type.value)
        child = Task(
            task_id=asset_task_id(next_type, asset_id, input_version=input_version),
            task_type=next_type,
            target_kind="asset",
            target_id=asset_id,
            depends_on=(DependencySpec(task_id=task.task_id, required_status=TaskStatus.SUCCESS),),
            input_version=input_version,
            artifacts_summary=child_task_artifacts(task, {"card_id": task.target_id}),
        )
        repo.enqueue(child)
        created.append(child.task_id)
    if not created:
        return enqueue_card_finalize(
            repo,
            task.target_id,
            dependency_task_id=task.task_id,
            parent_task=task,
            base_dir=base_dir,
        )
    return created


def enqueue_video_transcribe(
    repo: TaskRepository,
    task: Task,
    artifacts: dict[str, Any],
    base_dir: Path,
) -> list[str]:
    audio_path = str(artifacts.get("audio_path") or "")
    if not audio_path:
        return []
    asset = _durable_asset(base_dir, task.target_id)
    if asset is None:
        return []
    input_version = video_transcript_input_version(audio_path=audio_path, extract_version=task.input_version)
    child = Task(
        task_id=asset_task_id(TaskType.VIDEO_TRANSCRIBE, task.target_id, input_version=input_version),
        task_type=TaskType.VIDEO_TRANSCRIBE,
        target_kind="asset",
        target_id=task.target_id,
        depends_on=(DependencySpec(task_id=task.task_id, required_status=TaskStatus.SUCCESS),),
        input_version=input_version,
        artifacts_summary=child_task_artifacts(
            task,
            {"card_id": asset["card_id"], "audio_path": audio_path, "duration_seconds": artifacts.get("duration_seconds"), "format": artifacts.get("format")},
        ),
    )
    repo.enqueue(child)
    return [child.task_id]


def _durable_card(base_dir: Path, card_id: str) -> dict[str, Any] | None:
    if not card_id:
        return None
    from backend.infrastructure.storage.repositories import CardRepository

    return CardRepository(base_dir).get_card(card_id)


def _durable_asset(base_dir: Path, asset_id: str) -> dict[str, Any] | None:
    if not asset_id:
        return None
    from backend.infrastructure.storage.repositories import CardAssetRepository

    return CardAssetRepository(base_dir).get_asset(asset_id)
