from __future__ import annotations

from dataclasses import replace

import pytest

from backend.application.tasks.batches import create_task_batch, get_task_batch
from backend.application.tasks.planner import (
    raw_item_target_id,
    raw_item_task_id,
    versioned_task_id,
)
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
    RawItemRepository,
    SourceRepository,
)


def _raw_item(raw_item_id: str = "raw_reconcile") -> RawItem:
    return RawItem(
        raw_item_id=raw_item_id,
        connector_id="rss_reconcile",
        source_platform="rss",
        source_type="article",
        source_url=f"https://example.com/{raw_item_id}",
        captured_at="2026-07-10T08:00:00Z",
        raw_text="A durable raw item for pipeline reconciliation.",
        raw_metadata={"source_id": "rss_reconcile", "title": "Reconcile source"},
    )


def _card(card_id: str = "card_reconcile") -> dict[str, object]:
    return {
        "card_id": card_id,
        "source_id": "rss_reconcile",
        "source_platform": "rss",
        "source_type": "article",
        "source_url": f"https://example.com/{card_id}",
        "source_author": "Example",
        "title": "Pipeline reconciliation",
        "content": "Durable card content used to verify current-version task planning.",
        "captured_at": "2026-07-10T08:00:00Z",
        "processed_at": "2026-07-10T08:00:00Z",
        "raw_payload": {},
    }


def _seed_source(base_dir) -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": "rss_reconcile",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/reconcile.xml",
            "display_name": "Reconcile RSS",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_reconcile",
        }
    )


def _seed_terminal_card_upstream(base_dir, card: dict[str, object]) -> None:
    from backend.application.tasks.input_versions import enrichment_input_version, media_input_version

    durable_card = CardRepository(base_dir).get_card(str(card["card_id"]))
    assert durable_card is not None
    card_id = str(durable_card["card_id"])
    enrich_version = enrichment_input_version(durable_card)
    enrich_task = Task(
        task_id=versioned_task_id(TaskType.CONTENT_ENRICH, card_id, enrich_version),
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id=card_id,
        status=TaskStatus.SUCCESS,
        input_version=enrich_version,
    )
    media_task = Task(
        task_id=versioned_task_id(
            TaskType.MEDIA_DOWNLOAD,
            card_id,
            media_input_version(card=durable_card, enrichment_version=enrich_version),
        ),
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id=card_id,
        status=TaskStatus.SUCCESS,
        input_version=media_input_version(card=durable_card, enrichment_version=enrich_version),
        depends_on=(DependencySpec(task_id=enrich_task.task_id, required_status=TaskStatus.SUCCESS),),
    )
    repo = TaskRepository.for_base_dir(base_dir)
    repo.enqueue(enrich_task)
    repo.enqueue(media_task)


def _seed_current_successful_pipeline(base_dir, card_id: str) -> None:
    from backend.application.tasks.input_versions import card_finalize_input_version

    card = CardRepository(base_dir).get_card(card_id)
    assert card is not None
    _seed_terminal_card_upstream(base_dir, card)
    repo = TaskRepository.for_base_dir(base_dir)
    for task_type in (TaskType.CONTENT_ENRICH, TaskType.MEDIA_DOWNLOAD):
        for task in repo.list_by_target(
            target_kind="card", target_id=card_id, task_type=task_type
        ):
            repo.update_status(task.task_id, TaskStatus.SUCCESS)
    blocks = CardContentBlockRepository(base_dir).list_for_card(card_id)
    assets = CardAssetRepository(base_dir).list_for_card(card_id)
    finalize_version = card_finalize_input_version(card, blocks, assets)
    finalize = Task(
        task_id=versioned_task_id(TaskType.CARD_FINALIZE, card_id, finalize_version),
        task_type=TaskType.CARD_FINALIZE,
        target_kind="card",
        target_id=card_id,
        status=TaskStatus.SUCCESS,
        input_version=finalize_version,
    )
    repo.enqueue(finalize)
    repo.update_status(finalize.task_id, TaskStatus.SUCCESS)


def test_reconcile_enqueues_missing_card_build_for_raw_item(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    _seed_source(tmp_path)
    raw_item = _raw_item()
    RawItemRepository(tmp_path).upsert_raw_items([raw_item])

    result = reconcile_pipeline(tmp_path)

    task = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="raw_item",
        target_id=raw_item_target_id(raw_item.connector_id, raw_item.raw_item_id),
        task_type=TaskType.CARD_BUILD,
    )
    assert len(task) == 1
    assert task[0].task_id == raw_item_task_id(
        TaskType.CARD_BUILD,
        raw_item.raw_item_id,
        connector_id=raw_item.connector_id,
        input_version=task[0].input_version,
    )
    assert result["created_by_type"] == {TaskType.CARD_BUILD.value: 1}


def test_reconcile_keyset_cursor_advances_beyond_one_scan_budget(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    _seed_source(tmp_path)
    raw_items = [_raw_item(f"raw_cursor_{index}") for index in range(4)]
    RawItemRepository(tmp_path).upsert_raw_items(raw_items)

    results = [reconcile_pipeline(tmp_path, limit=3) for _ in range(4)]
    tasks = TaskRepository.for_base_dir(tmp_path).list_tasks(
        task_type=TaskType.CARD_BUILD,
        limit=20,
    )

    assert [result["created_by_type"] for result in results] == [
        {TaskType.CARD_BUILD.value: 1},
        {TaskType.CARD_BUILD.value: 1},
        {TaskType.CARD_BUILD.value: 1},
        {TaskType.CARD_BUILD.value: 1},
    ]
    assert {task.target_id for task in tasks} == {
        raw_item_target_id(item.connector_id, item.raw_item_id) for item in raw_items
    }


def test_reconcile_skips_objects_owned_by_a_deleted_source(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    raw_item = _raw_item("raw_deleted_source")
    card = _card("card_deleted_source") | {
        "source_id": None,
        "raw_payload": {"source_id": "rss_reconcile"},
    }
    RawItemRepository(tmp_path).upsert_raw_items([raw_item])
    CardRepository(tmp_path).upsert_card(card)

    result = reconcile_pipeline(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)

    assert repo.list_by_target(
        target_kind="raw_item",
        target_id=raw_item_target_id(raw_item.connector_id, raw_item.raw_item_id),
        task_type=TaskType.CARD_BUILD,
    ) == []
    assert repo.list_by_target(
        target_kind="card",
        target_id=str(card["card_id"]),
    ) == []
    assert result["created_by_type"] == {}


def test_reconcile_finalizes_new_card_version_with_delivered_assets_from_an_older_chain(
    tmp_path,
) -> None:
    from backend.application.tasks.input_versions import enrichment_input_version, media_input_version
    from backend.application.tasks.transitions import enqueue_media_subtasks
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_cross_chain_assets"
    _seed_source(tmp_path)
    CardRepository(tmp_path).upsert_card(_card(card_id))
    for asset_id, kind in (("asset_current_image", "image"), ("asset_old_video", "video")):
        CardAssetRepository(tmp_path).upsert_asset({
            "asset_id": asset_id,
            "card_id": card_id,
            "asset_kind": kind,
            "source_url": f"https://example.com/{asset_id}",
            "local_path": f"{card_id}/{asset_id}",
            "status": "downloaded",
        })
    card = CardRepository(tmp_path).get_card(card_id)
    enrich_version = enrichment_input_version(card)
    enrich = Task(
        task_id=versioned_task_id(TaskType.CONTENT_ENRICH, card_id, enrich_version),
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id=card_id,
        status=TaskStatus.SUCCESS,
        input_version=enrich_version,
        artifacts_summary={"chain_id": "chain_current"},
    )
    media_version = media_input_version(card=card, enrichment_version=enrich_version)
    media = Task(
        task_id=versioned_task_id(TaskType.MEDIA_DOWNLOAD, card_id, media_version),
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id=card_id,
        status=TaskStatus.SUCCESS,
        input_version=media_version,
        artifacts_summary={
            "chain_id": "chain_current",
            "card_id": card_id,
            "assets": [{"asset_id": "asset_current_image"}],
            "download_status": "downloaded",
        },
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(enrich)
    repo.enqueue(media)
    repo.enqueue(Task(
        task_id="video_transcribe_old_chain",
        task_type=TaskType.VIDEO_TRANSCRIBE,
        target_kind="asset",
        target_id="asset_old_video",
        status=TaskStatus.SUCCESS,
        artifacts_summary={"chain_id": "chain_old", "card_id": card_id},
    ))
    finalize_task_id = enqueue_media_subtasks(
        repo, media, media.artifacts_summary, tmp_path
    )[0]
    assert repo.get(finalize_task_id).task_type == TaskType.CARD_FINALIZE
    assert repo.list_tasks(task_type=TaskType.IMAGE_UNDERSTAND) == []

    result = reconcile_pipeline(tmp_path)
    finalize_tasks = repo.list_by_target(
        target_kind="card", target_id=card_id, task_type=TaskType.CARD_FINALIZE
    )

    assert result["created_by_type"].get(TaskType.IMAGE_UNDERSTAND.value, 0) == 0
    assert len(finalize_tasks) == 1
    run_due_tasks(tmp_path, task_types=[TaskType.CARD_FINALIZE], max_workers=1)
    assert CardRepository(tmp_path).get_card(card_id)["status"] == "active"


def test_reconcile_requeues_card_finalize_after_policy_change(tmp_path) -> None:
    from backend.application.tasks.input_versions import card_finalize_input_version
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_legacy_finalize_policy"
    _seed_source(tmp_path)
    CardRepository(tmp_path).upsert_card(_card(card_id))
    card = CardRepository(tmp_path).get_card(card_id)
    assert card is not None
    _seed_terminal_card_upstream(tmp_path, card)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="card_finalize_card_legacy_finalize_policy_legacy",
            task_type=TaskType.CARD_FINALIZE,
            target_kind="card",
            target_id=card_id,
            status=TaskStatus.SUCCESS,
            input_version="legacy-finalize-policy",
        )
    )

    result = reconcile_pipeline(tmp_path)

    finalize_tasks = repo.list_by_target(
        target_kind="card", target_id=card_id, task_type=TaskType.CARD_FINALIZE
    )
    current_version = card_finalize_input_version(card, [], [])
    assert result["created_by_type"] == {TaskType.CARD_FINALIZE.value: 1}
    assert {task.input_version for task in finalize_tasks} == {
        "legacy-finalize-policy",
        current_version,
    }
    assert next(task for task in finalize_tasks if task.input_version == current_version).status == TaskStatus.PENDING


def test_reconcile_repairs_card_reset_after_successful_finalize(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_terminal_state_reset"
    _seed_source(tmp_path)
    CardRepository(tmp_path).upsert_card(_card(card_id))
    card = CardRepository(tmp_path).get_card(card_id)
    assert card is not None
    _seed_terminal_card_upstream(tmp_path, card)
    reconcile_pipeline(tmp_path)
    run_due_tasks(tmp_path, task_types=[TaskType.CARD_FINALIZE], max_workers=1)
    with CardRepository(tmp_path).connect() as conn:
        conn.execute(
            """
            update cards
            set status = 'processing', completeness_status = 'processing',
              missing_reasons = '[]'
            where card_id = ?
            """,
            (card_id,),
        )

    result = reconcile_pipeline(tmp_path)
    repaired = CardRepository(tmp_path).get_card(card_id)

    assert repaired is not None
    assert repaired["status"] == "active"
    assert repaired["completeness_status"] == "complete"
    assert result["created_by_type"] == {}


def test_reconcile_prioritizes_terminal_state_repair_ahead_of_card_cursor(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
    from backend.infrastructure.storage.reconciliation_cursors import (
        ReconciliationCursorRepository,
    )

    target_id = "card_000_terminal_state_reset"
    _seed_source(tmp_path)
    CardRepository(tmp_path).upsert_card(_card(target_id))
    card = CardRepository(tmp_path).get_card(target_id)
    assert card is not None
    _seed_terminal_card_upstream(tmp_path, card)
    reconcile_pipeline(tmp_path)
    run_due_tasks(tmp_path, task_types=[TaskType.CARD_FINALIZE], max_workers=1)
    with CardRepository(tmp_path).connect() as conn:
        conn.execute(
            """
            update cards
            set status = 'processing', completeness_status = 'processing',
              missing_reasons = '[]'
            where card_id = ?
            """,
            (target_id,),
        )
    CardRepository(tmp_path).upsert_card(_card("card_zzz_cursor_distractor"))
    ReconciliationCursorRepository(tmp_path).set("cards", "card_100_after_target")

    result = reconcile_pipeline(tmp_path, limit=3)
    repaired = CardRepository(tmp_path).get_card(target_id)

    assert repaired is not None
    assert repaired["status"] == "active"
    assert result["terminal_cards_repaired"] == 1


def test_reconcile_enqueues_missing_initial_fetch_for_enabled_uncalibrated_source(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    source = {
        "source_id": "rss_uncalibrated",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/uncalibrated.xml",
        "display_name": "Uncalibrated RSS",
        "cadence_hours": 24,
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": "rss_uncalibrated",
    }
    SourceRepository(tmp_path).upsert_source(source)

    first = reconcile_pipeline(tmp_path, limit=3)
    second = reconcile_pipeline(tmp_path, limit=3)
    tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="source",
        target_id="rss_uncalibrated",
        task_type=TaskType.FETCH,
    )

    assert first["created_by_type"][TaskType.FETCH.value] == 1
    assert first["calibration_sources_scanned"] == 1
    assert second["created_by_type"].get(TaskType.FETCH.value, 0) == 0
    assert len(tasks) == 1
    assert tasks[0].artifacts_summary["initial_fetch"] is True


def test_reconcile_recovers_failed_current_initial_fetch_only_once(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
    from backend.application.workflows.source_initial_fetch import (
        enqueue_or_reuse_initial_fetch,
    )

    source = {
        "source_id": "youtube_terminal_initial",
        "platform": "youtube",
        "source_type": "channel",
        "identifier": "https://www.youtube.com/@example",
        "display_name": "Terminal initial fetch",
        "cadence_hours": 24,
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": "youtube_terminal_initial",
    }
    source_repo = SourceRepository(tmp_path)
    source_repo.upsert_source(source)
    stored = source_repo.get_source(source["source_id"])
    assert stored is not None
    queued = enqueue_or_reuse_initial_fetch(
        tmp_path,
        stored,
        now="2026-07-30T00:00:00Z",
        reason="test_seed",
    )
    task_id = str(queued["task_id"])
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.update_status(
        task_id,
        TaskStatus.FAILED,
        error_reason="opencli_command_failed",
    )

    reconcile_pipeline(tmp_path, limit=3)
    recovered = repo.get(task_id)

    assert recovered is not None
    assert recovered.status is TaskStatus.PENDING
    assert recovered.attempt == 1
    assert recovered.error_reason is None
    assert recovered.artifacts_summary["terminal_recovery_keys"] == [
        "pipeline-reconciliation-v1"
    ]

    repo.update_status(
        task_id,
        TaskStatus.FAILED,
        error_reason="opencli_command_failed",
    )
    reconcile_pipeline(tmp_path, limit=3)

    assert repo.get(task_id).status is TaskStatus.FAILED


def test_reconcile_skips_existing_calibration_without_consuming_write_budget(tmp_path) -> None:
    from backend.application.tasks.planner import enqueue_source_calibration_task
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    def source(source_id: str, display_name: str) -> dict:
        return {
            "source_id": source_id,
            "platform": "rss",
            "source_type": "feed",
            "identifier": f"https://example.com/{source_id}.xml",
            "display_name": display_name,
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "implemented_uncalibrated",
            "connector_id": source_id,
        }

    first = source("rss_existing", "A existing")
    second = source("rss_missing", "B missing")
    SourceRepository(tmp_path).upsert_source(first)
    SourceRepository(tmp_path).upsert_source(second)
    enqueue_source_calibration_task(tmp_path, first, now="2026-07-11T08:00:00Z")

    result = reconcile_pipeline(tmp_path, limit=1)

    assert result["created_by_type"][TaskType.FETCH.value] == 1
    assert TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="source",
        target_id="rss_missing",
        task_type=TaskType.FETCH,
    )


def test_reconcile_ignores_stale_calibration_task_after_source_change(tmp_path) -> None:
    from backend.application.tasks.input_versions import source_calibration_input_version
    from backend.application.tasks.planner import enqueue_source_calibration_task
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    source = {
        "source_id": "rss_changed",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/old.xml",
        "display_name": "Changed RSS",
        "cadence_hours": 24,
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": "rss_changed",
    }
    repository = SourceRepository(tmp_path)
    repository.upsert_source(source)
    enqueue_source_calibration_task(tmp_path, source, now="2026-07-11T08:00:00Z")
    changed = {**source, "identifier": "https://example.com/new.xml"}
    repository.upsert_source(changed)

    result = reconcile_pipeline(tmp_path, limit=1)
    tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="source",
        target_id="rss_changed",
        task_type=TaskType.SOURCE_CALIBRATE,
    )

    assert result["created_by_type"][TaskType.FETCH.value] == 1
    initial_tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="source", target_id="rss_changed", task_type=TaskType.FETCH
    )
    assert {task.input_version for task in initial_tasks} == {source_calibration_input_version(changed)}
    assert {task.input_version for task in tasks} == {source_calibration_input_version(source)}


def test_reconcile_enqueues_missing_content_task_for_card(tmp_path) -> None:
    from backend.application.tasks.input_versions import enrichment_input_version
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_missing_content"
    _seed_source(tmp_path)
    CardRepository(tmp_path).upsert_card(_card(card_id))

    result = reconcile_pipeline(tmp_path)

    card = CardRepository(tmp_path).get_card(card_id)
    assert card is not None
    input_version = enrichment_input_version(card)
    task = TaskRepository.for_base_dir(tmp_path).get(
        versioned_task_id(TaskType.CONTENT_ENRICH, card_id, input_version)
    )
    assert task is not None
    assert result["created_by_type"][TaskType.CONTENT_ENRICH.value] == 1


def test_reconcile_reserves_card_scan_budget_when_raw_backlog_is_full(tmp_path) -> None:
    from backend.application.tasks.input_versions import enrichment_input_version
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_reconcile_after_raw_backlog"
    _seed_source(tmp_path)
    CardRepository(tmp_path).upsert_card(_card(card_id))
    RawItemRepository(tmp_path).upsert_raw_items(
        [_raw_item(f"raw_reconcile_backlog_{index}") for index in range(4)]
    )

    result = reconcile_pipeline(tmp_path, limit=3)

    card = CardRepository(tmp_path).get_card(card_id)
    assert card is not None
    assert TaskRepository.for_base_dir(tmp_path).get(
        versioned_task_id(TaskType.CONTENT_ENRICH, card_id, enrichment_input_version(card))
    ) is not None
    assert result["scanned"] <= 3


def test_reconcile_preserves_historical_context_without_scheduling_retired_stages(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_context_changed"
    _seed_source(tmp_path)
    source_card = _card(card_id)
    CardRepository(tmp_path).upsert_card(source_card)
    _seed_terminal_card_upstream(tmp_path, source_card)
    block_repo = CardContentBlockRepository(tmp_path)
    block_repo.upsert_block(
        {
            "block_id": f"block_{card_id}_enrichment",
            "card_id": card_id,
            "position": 0,
            "layer": "derived",
            "block_kind": "enrichment",
            "status": "enriched",
            "body": "old enriched evidence",
            "provenance": "test",
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    durable_card = CardRepository(tmp_path).get_card(card_id)
    assert durable_card is not None
    old_context_version = "historical-context-v1"
    old_context_block_id = f"block_{card_id}_llm_context"
    repo.enqueue(
        Task(
            task_id=versioned_task_id(TaskType.CONTEXT_PACK, card_id, old_context_version),
            task_type=TaskType.CONTEXT_PACK,
            target_kind="card",
            target_id=card_id,
            status=TaskStatus.SUCCESS,
            input_version=old_context_version,
            artifacts_summary={"context_block_id": old_context_block_id},
        )
    )
    block_repo.upsert_block(
        {
            "block_id": old_context_block_id,
            "card_id": card_id,
            "position": 1,
            "layer": "derived",
            "block_kind": "llm_context",
            "status": "enriched",
            "body": "old durable context",
            "provenance": "test",
            "raw_payload": {"context_input_version": old_context_version},
        }
    )
    block_repo.upsert_block(
        {
            "block_id": f"block_{card_id}_enrichment",
            "card_id": card_id,
            "position": 0,
            "layer": "derived",
            "block_kind": "enrichment",
            "status": "enriched",
            "body": "new enriched evidence",
            "provenance": "test",
        }
    )

    result = reconcile_pipeline(tmp_path)

    context_tasks = repo.list_by_target(
        target_kind="card", target_id=card_id, task_type=TaskType.CONTEXT_PACK
    )
    assert [task.input_version for task in context_tasks] == [old_context_version]
    assert repo.list_by_target(target_kind="card", target_id=card_id, task_type=TaskType.SUMMARY) == []
    assert result["created_by_type"] == {TaskType.CARD_FINALIZE.value: 1}


def test_reconcile_does_not_duplicate_current_version_tasks(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    _seed_source(tmp_path)
    raw_item = _raw_item("raw_no_duplicates")
    RawItemRepository(tmp_path).upsert_raw_items([raw_item])

    first = reconcile_pipeline(tmp_path)
    second = reconcile_pipeline(tmp_path)

    tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="raw_item",
        target_id=raw_item_target_id(raw_item.connector_id, raw_item.raw_item_id),
        task_type=TaskType.CARD_BUILD,
    )
    assert first["created_by_type"] == {TaskType.CARD_BUILD.value: 1}
    assert second["created_by_type"] == {}
    assert len(tasks) == 1


def test_reconcile_repairs_missing_batch_attachment(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    repo = TaskRepository.for_base_dir(tmp_path)
    parent = Task(
        task_id="summary_batch_parent",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="card_batch",
        artifacts_summary={"downstream_task_ids": ["semantic_batch_child"]},
    )
    child = Task(
        task_id="semantic_batch_child",
        task_type=TaskType.SEMANTIC_EXTRACT,
        target_kind="card",
        target_id="card_batch",
    )
    repo.enqueue(parent)
    repo.enqueue(child)
    batch = create_task_batch(tmp_path, batch_type="manual", task_ids=[parent.task_id])

    result = reconcile_pipeline(tmp_path)

    repaired = get_task_batch(tmp_path, batch["batch_id"])
    assert repaired["task_ids"] == [parent.task_id, child.task_id]
    assert result["batch_links_repaired"] == 1


def test_batch_link_cursor_wraps_past_continuously_newer_parents(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
    from backend.infrastructure.storage.reconciliation_cursors import ReconciliationCursorRepository

    repo = TaskRepository.for_base_dir(tmp_path)
    old_parent = Task(
        task_id="a_old_parent",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="old-card",
        artifacts_summary={"downstream_task_ids": ["a_old_child"]},
    )
    old_child = Task(
        task_id="a_old_child",
        task_type=TaskType.SEMANTIC_EXTRACT,
        target_kind="card",
        target_id="old-card",
    )
    repo.enqueue(old_parent)
    repo.enqueue(old_child)
    newest = Task(
        task_id="z_parent_00",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="new-card",
        artifacts_summary={"downstream_task_ids": ["a_old_child"]},
    )
    repo.enqueue(newest)
    batch = create_task_batch(tmp_path, batch_type="manual", task_ids=[old_parent.task_id])
    ReconciliationCursorRepository(tmp_path).set(
        "batch_links", '{"after":"m_cursor","upper":"z_parent_00"}'
    )

    for index in range(1, 5):
        repo.enqueue(Task(
            task_id=f"z_parent_{index:02d}",
            task_type=TaskType.SUMMARY,
            target_kind="card",
            target_id=f"new-card-{index}",
            artifacts_summary={"downstream_task_ids": ["a_old_child"]},
        ))
        reconcile_pipeline(tmp_path, limit=3)
        if old_child.task_id in get_task_batch(tmp_path, batch["batch_id"])["task_ids"]:
            break

    repaired = get_task_batch(tmp_path, batch["batch_id"])
    assert repaired["task_ids"] == [old_parent.task_id, old_child.task_id]


def test_reconcile_defers_context_until_media_is_terminal_and_keeps_failure_gap(tmp_path) -> None:
    from backend.application.tasks.input_versions import enrichment_input_version, media_input_version
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_pending_media_reconcile"
    _seed_source(tmp_path)
    CardRepository(tmp_path).upsert_card(_card(card_id))
    card = CardRepository(tmp_path).get_card(card_id)
    assert card is not None

    enrich_version = enrichment_input_version(card)
    enrich_task = Task(
        task_id=versioned_task_id(TaskType.CONTENT_ENRICH, card_id, enrich_version),
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id=card_id,
        input_version=enrich_version,
    )
    media_task = Task(
        task_id=versioned_task_id(
            TaskType.MEDIA_DOWNLOAD,
            card_id,
            media_input_version(card=card, enrichment_version=enrich_version),
        ),
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id=card_id,
        input_version=media_input_version(card=card, enrichment_version=enrich_version),
        depends_on=(DependencySpec(task_id=enrich_task.task_id, required_status=TaskStatus.SUCCESS),),
        artifacts_summary={"card_id": card_id, "gap_reasons": ["download_unavailable"]},
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(enrich_task)
    repo.enqueue(media_task)

    pending = reconcile_pipeline(tmp_path)

    assert pending["created_by_type"] == {}
    assert repo.list_by_target(target_kind="card", target_id=card_id, task_type=TaskType.CONTEXT_PACK) == []
    assert repo.list_by_target(target_kind="card", target_id=card_id, task_type=TaskType.SUMMARY) == []

    repo.update_status(enrich_task.task_id, TaskStatus.SUCCESS)
    repo.update_status(media_task.task_id, TaskStatus.FAILED, error_reason="download_unavailable")

    repaired = reconcile_pipeline(tmp_path)

    finalize_tasks = repo.list_by_target(target_kind="card", target_id=card_id, task_type=TaskType.CARD_FINALIZE)
    assert repaired["created_by_type"] == {TaskType.CARD_FINALIZE.value: 1}
    assert len(finalize_tasks) == 1
    finalize = finalize_tasks[0]
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["media_gaps"] == [
        {
            "missing_part": "media_download",
            "reason": "download_unavailable",
            "status": TaskStatus.FAILED.value,
            "task_id": media_task.task_id,
        }
    ]
    assert reconcile_pipeline(tmp_path)["created_by_type"] == {}


def test_reconcile_defers_summary_while_current_enrichment_or_media_is_pending(tmp_path) -> None:
    from backend.application.tasks.input_versions import enrichment_input_version, media_input_version
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_pending_summary_reconcile"
    _seed_source(tmp_path)
    CardRepository(tmp_path).upsert_card(_card(card_id))
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": f"block_{card_id}_llm_context",
            "card_id": card_id,
            "position": 0,
            "layer": "derived",
            "block_kind": "llm_context",
            "status": "enriched",
            "body": "existing context must not bypass pending media",
            "provenance": "test",
        }
    )
    card = CardRepository(tmp_path).get_card(card_id)
    assert card is not None
    enrich_version = enrichment_input_version(card)
    enrich_task = Task(
        task_id=versioned_task_id(TaskType.CONTENT_ENRICH, card_id, enrich_version),
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id=card_id,
        input_version=enrich_version,
    )
    media_task = Task(
        task_id=versioned_task_id(
            TaskType.MEDIA_DOWNLOAD,
            card_id,
            media_input_version(card=card, enrichment_version=enrich_version),
        ),
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id=card_id,
        input_version=media_input_version(card=card, enrichment_version=enrich_version),
        depends_on=(DependencySpec(task_id=enrich_task.task_id, required_status=TaskStatus.SUCCESS),),
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(enrich_task)
    repo.enqueue(media_task)

    result = reconcile_pipeline(tmp_path)

    assert result["created_by_type"] == {}
    assert repo.list_by_target(target_kind="card", target_id=card_id, task_type=TaskType.SUMMARY) == []


def test_youtube_legacy_description_reconciles_once_with_new_policy_identity(tmp_path) -> None:
    from backend.application.tasks.input_versions import InputSnapshot, enrichment_input_version, media_input_version
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_youtube_legacy_description"
    _seed_source(tmp_path)
    card = {
        **_card(card_id),
        "source_platform": "youtube",
        "source_type": "video",
        "source_url": "https://www.youtube.com/watch?v=legacy123",
    }
    CardRepository(tmp_path).upsert_card(card)
    durable = CardRepository(tmp_path).get_card(card_id)
    assert durable is not None
    old_version = InputSnapshot("card", card_id, "content_enrich", durable).version()
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id=versioned_task_id(TaskType.CONTENT_ENRICH, card_id, old_version),
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id=card_id,
            status=TaskStatus.SUCCESS,
            input_version=old_version,
        )
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": f"block_{card_id}_transcript",
            "card_id": card_id,
            "position": 0,
            "layer": "context",
            "block_kind": "transcript",
            "status": "enriched",
            "body": "legacy title posing as description",
            "provenance": "YouTube description fallback",
            "raw_payload": {},
        }
    )

    first = reconcile_pipeline(tmp_path)
    second = reconcile_pipeline(tmp_path)
    current_version = enrichment_input_version(durable)
    current = repo.get(versioned_task_id(TaskType.CONTENT_ENRICH, card_id, current_version))
    assert current is not None and current.status == TaskStatus.PENDING
    assert current_version != old_version
    assert media_input_version(card=durable, enrichment_version=current_version) != media_input_version(
        card=durable, enrichment_version=old_version
    )
    assert first["created_by_type"][TaskType.CONTENT_ENRICH.value] == 1
    assert second["created_by_type"].get(TaskType.CONTENT_ENRICH.value, 0) == 0
    assert len(repo.list_by_target(target_kind="card", target_id=card_id, task_type=TaskType.CONTENT_ENRICH)) == 2


def test_youtube_terminal_subtitle_outcome_prevents_reconciliation_repeat(tmp_path) -> None:
    from backend.application.enrichment.transcripts.subtitles import SUBTITLE_POLICY_VERSION
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    card_id = "card_youtube_terminal_subtitle"
    _seed_source(tmp_path)
    CardRepository(tmp_path).upsert_card(
        {
            **_card(card_id),
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://youtu.be/demoYt00004",
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="legacy-youtube-enrich",
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id=card_id,
            status=TaskStatus.SUCCESS,
            input_version="legacy",
        )
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": f"block_{card_id}_youtube_subtitle_attempt",
            "card_id": card_id,
            "position": 0,
            "layer": "context",
            "block_kind": "enrichment",
            "status": "done",
            "body": "",
            "provenance": "YouTube subtitle attempt",
            "raw_payload": {
                "subtitle_attempt_status": "unavailable",
                "subtitle_policy_version": SUBTITLE_POLICY_VERSION,
                "usable_transcript": False,
            },
        }
    )
    reconcile_pipeline(tmp_path)
    reconcile_pipeline(tmp_path)
    tasks = repo.list_by_target(target_kind="card", target_id=card_id, task_type=TaskType.CONTENT_ENRICH)
    assert [task.task_id for task in tasks] == ["legacy-youtube-enrich"]


def test_reconcile_backfills_current_llm_policy_only_for_explicitly_enabled_sources(
    tmp_path,
) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_reconcile",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/reconcile.xml",
            "display_name": "Reconcile RSS",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_reconcile",
            "raw_metadata": {
                "ai_processing": {
                    "enabled": True,
                    "provider_id": "provider-default",
                    "model": "model-default",
                    "target_language": "zh-CN",
                }
            },
        }
    )
    CardRepository(tmp_path).upsert_card(_card("card_llm_policy_backfill"))
    with CardRepository(tmp_path).connect() as conn:
        conn.execute(
            "update cards set status = 'active', completeness_status = 'complete' where card_id = ?",
            ("card_llm_policy_backfill",),
        )
    _seed_current_successful_pipeline(tmp_path, "card_llm_policy_backfill")

    first = reconcile_pipeline(tmp_path, limit=3)
    second = reconcile_pipeline(tmp_path, limit=3)
    tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="card",
        target_id="card_llm_policy_backfill",
        task_type=TaskType.CARD_LLM_ENRICH,
    )

    assert tasks == []
    assert first["created_by_type"].get(TaskType.CARD_LLM_ENRICH.value, 0) == 0
    assert second["created_by_type"].get(TaskType.CARD_LLM_ENRICH.value, 0) == 0


def test_reconcile_never_backfills_paid_llm_before_current_pipeline_is_finalized(
    tmp_path,
) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_reconcile",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/reconcile.xml",
            "display_name": "Reconcile RSS",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_reconcile",
            "raw_metadata": {
                "ai_processing": {
                    "enabled": True,
                    "provider_id": "provider-default",
                    "model": "model-default",
                    "target_language": "zh-CN",
                }
            },
        }
    )
    CardRepository(tmp_path).upsert_card(_card("card_llm_not_ready"))
    with CardRepository(tmp_path).connect() as conn:
        conn.execute(
            "update cards set status = 'active' where card_id = ?",
            ("card_llm_not_ready",),
        )

    result = reconcile_pipeline(tmp_path, limit=3)
    repo = TaskRepository.for_base_dir(tmp_path)

    assert repo.list_by_target(
        target_kind="card",
        target_id="card_llm_not_ready",
        task_type=TaskType.CARD_LLM_ENRICH,
    ) == []
    assert result["created_by_type"].get(TaskType.CARD_LLM_ENRICH.value, 0) == 0
    assert result["created_by_type"][TaskType.CONTENT_ENRICH.value] == 1


def test_reconcile_waits_for_required_video_transcript_before_llm_backfill(tmp_path) -> None:
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline

    _seed_source(tmp_path)
    source = SourceRepository(tmp_path).get_source("rss_reconcile")
    assert source is not None
    source["raw_metadata"] = {
        "ai_processing": {
            "enabled": True,
            "provider_id": "provider-default",
            "model": "model-default",
            "target_language": "zh-CN",
        }
    }
    SourceRepository(tmp_path).upsert_source(source)
    card_id = "card_video_llm_waits"
    CardRepository(tmp_path).upsert_card(_card(card_id))
    with CardRepository(tmp_path).connect() as conn:
        conn.execute(
            "update cards set status = 'active', completeness_status = 'partial' where card_id = ?",
            (card_id,),
        )
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video_waiting",
            "card_id": card_id,
            "asset_kind": "video",
            "source_url": "https://example.com/video.mp4",
            "status": "downloaded",
        }
    )

    first = reconcile_pipeline(tmp_path, limit=3)
    repo = TaskRepository.for_base_dir(tmp_path)
    assert repo.list_by_target(
        target_kind="card", target_id=card_id, task_type=TaskType.CARD_LLM_ENRICH
    ) == []
    assert first["created_by_type"].get(TaskType.CARD_LLM_ENRICH.value, 0) == 0

    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "video_transcript_waiting",
            "card_id": card_id,
            "position": 0,
            "layer": "derived",
            "block_kind": "video_transcript",
            "status": "enriched",
            "body": "完整视频文稿",
            "source_url": "asset:video_waiting",
            "provenance": "test",
            "raw_payload": {"asset_id": "video_waiting", "language": "zh"},
        }
    )
    _seed_current_successful_pipeline(tmp_path, card_id)
    second = reconcile_pipeline(tmp_path, limit=3)
    tasks = repo.list_by_target(
        target_kind="card", target_id=card_id, task_type=TaskType.CARD_LLM_ENRICH
    )
    assert tasks == []
    assert second["created_by_type"].get(TaskType.CARD_LLM_ENRICH.value, 0) == 0


@pytest.mark.parametrize(
    "retry_reason",
    ["external_doc_browser_unavailable", "external_doc_adapter_failed"],
)
def test_reconcile_retries_scys_external_doc_and_versions_downstream_media(
    tmp_path, monkeypatch, retry_reason
) -> None:
    from backend.application.enrichment.external_docs import enrich_external_docs
    from backend.application.tasks.input_versions import enrichment_input_version
    from backend.application.workflows.external_doc_reconciliation import (
        EXTERNAL_DOC_RETRY_POLICY_VERSION,
        MAX_EXTERNAL_DOC_RETRIES,
    )
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
    from backend.infrastructure.external_docs.feishu_browser import FeishuDocumentResult

    card_id = "card_scys_external_retry"
    CardRepository(tmp_path).upsert_card(
        _card(card_id)
        | {
            "source_id": None,
            "source_platform": "scys",
            "source_url": "https://scys.com/articleDetail/xq_topic/123",
            "content": "完整正文在飞书：https://example.feishu.cn/docx/RETRY",
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(
            status="pending", source_url=url, reason=retry_reason
        ),
    )
    assert enrich_external_docs(card_id, tmp_path) == 1
    block_repo = CardContentBlockRepository(tmp_path)
    existing_block = next(
        block
        for block in block_repo.list_for_card(card_id)
        if block["block_kind"] == "external_doc"
    )
    block_repo.upsert_block(
        existing_block
        | {
            "raw_payload": existing_block["raw_payload"]
            | {"retry_count": MAX_EXTERNAL_DOC_RETRIES}
        }
    )
    card = CardRepository(tmp_path).get_card(card_id)
    assert card is not None
    canonical_version = enrichment_input_version(card)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id=versioned_task_id(TaskType.CONTENT_ENRICH, card_id, canonical_version),
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id=card_id,
            status=TaskStatus.SUCCESS,
            input_version=canonical_version,
        )
    )

    result = reconcile_pipeline(tmp_path, limit=3)
    retry = next(
        task
        for task in repo.list_by_target(
            target_kind="card", target_id=card_id, task_type=TaskType.CONTENT_ENRICH
        )
        if task.artifacts_summary.get("external_doc_retry") is True
    )
    assert result["created_by_type"][TaskType.CONTENT_ENRICH.value] == 1
    assert (
        retry.artifacts_summary["external_doc_retry_policy_version"]
        == EXTERNAL_DOC_RETRY_POLICY_VERSION
    )
    while_pending = reconcile_pipeline(tmp_path, limit=3)
    assert while_pending["created_by_type"].get(TaskType.CONTENT_ENRICH.value, 0) == 0

    monkeypatch.setattr(
        "backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(
            status="fetched",
            source_url=url,
            body="恢复浏览器后取得的完整正文",
            images=["https://cdn.example.com/inside.jpg"],
            videos=["https://cdn.example.com/inside.mp4"],
        ),
    )
    processed = run_due_tasks(tmp_path, task_types=[TaskType.CONTENT_ENRICH], max_workers=1)
    assert processed["succeeded"] == 1
    block = next(
        block
        for block in CardContentBlockRepository(tmp_path).list_for_card(card_id)
        if block["block_kind"] == "external_doc"
    )
    assert block["status"] == "body_fetched"
    assert block["position"] == 0
    media_tasks = repo.list_by_target(
        target_kind="card", target_id=card_id, task_type=TaskType.MEDIA_DOWNLOAD
    )
    assert any(
        task.depends_on == (DependencySpec(retry.task_id, TaskStatus.SUCCESS),)
        for task in media_tasks
    )


def test_reconcile_repairs_fetched_scys_doc_with_missing_authenticated_media(
    tmp_path, monkeypatch
) -> None:
    from backend.application.tasks.input_versions import enrichment_input_version
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
    from backend.infrastructure.external_docs.feishu_browser import (
        FeishuDocumentResult,
        FeishuMediaBlob,
    )

    card_id = "card_scys_authenticated_media_retry"
    CardRepository(tmp_path).upsert_card(
        _card(card_id)
        | {
            "source_id": None,
            "source_platform": "scys",
            "source_url": "https://scys.com/articleDetail/xq_topic/456",
            "content": "完整正文在飞书：https://example.feishu.cn/docx/MEDIA",
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "external_doc_media_retry",
            "card_id": card_id,
            "position": 0,
            "layer": "external",
            "block_kind": "external_doc",
            "status": "body_fetched",
            "body": "已经取得的飞书正文",
            "source_url": "https://example.feishu.cn/docx/MEDIA",
            "provenance": "test",
            "raw_payload": {
                "provider": "feishu",
                "document_url": "https://example.feishu.cn/docx/MEDIA",
                "images": [
                    "https://internal-api-drive-stream.feishu.cn/image/1"
                ],
                "videos": [],
                "diagnostics": {"authenticated_media_count": 0},
                "authenticated_media_retry_count": 0,
            },
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "media_gap_media_retry",
            "card_id": card_id,
            "position": 1,
            "layer": "derived",
            "block_kind": "media_download_gap",
            "status": "blocked",
            "body": "媒体下载未完成",
            "source_url": "https://scys.com/articleDetail/xq_topic/456",
            "provenance": "test",
            "raw_payload": {"reason": "direct_media_download_failed"},
        }
    )

    card = CardRepository(tmp_path).get_card(card_id)
    assert card is not None
    repo = TaskRepository.for_base_dir(tmp_path)
    canonical_version = enrichment_input_version(card)
    repo.enqueue(
        Task(
            task_id=versioned_task_id(
                TaskType.CONTENT_ENRICH, card_id, canonical_version
            ),
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id=card_id,
            status=TaskStatus.SUCCESS,
            input_version=canonical_version,
        )
    )
    result = reconcile_pipeline(tmp_path, limit=3)
    retry = next(
        task
        for task in repo.list_by_target(
            target_kind="card", target_id=card_id, task_type=TaskType.CONTENT_ENRICH
        )
        if task.artifacts_summary.get("external_doc_retry") is True
    )
    assert result["created_by_type"][TaskType.CONTENT_ENRICH.value] == 1
    assert (
        retry.artifacts_summary["external_doc_retry_reason"]
        == "external_doc_authenticated_media_unavailable"
    )

    monkeypatch.setattr(
        "backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(
            status="fetched",
            source_url=url,
            body="恢复后取得的完整正文",
            images=["https://internal-api-drive-stream.feishu.cn/image/1"],
            media_blobs=[
                FeishuMediaBlob(
                    url="https://internal-api-drive-stream.feishu.cn/image/1",
                    kind="image",
                    mime_type="image/png",
                    content=b"\x89PNG\r\n\x1a\nrecovered",
                )
            ],
        ),
    )
    processed = run_due_tasks(
        tmp_path, task_types=[TaskType.CONTENT_ENRICH], max_workers=1
    )

    assert processed["succeeded"] == 1
    assert len(CardAssetRepository(tmp_path).list_for_card(card_id)) == 1
    assert any(
        task.depends_on == (DependencySpec(retry.task_id, TaskStatus.SUCCESS),)
        for task in repo.list_by_target(
            target_kind="card", target_id=card_id, task_type=TaskType.MEDIA_DOWNLOAD
        )
    )
