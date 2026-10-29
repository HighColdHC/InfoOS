from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from backend.application.tasks.input_versions import (
    asset_input_version, enrichment_input_version, media_input_version, source_calibration_input_version,
)
from backend.application.tasks.planner import asset_task_id, versioned_task_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.worker import run_tasks_by_ids
from backend.application.usecases.task_cancel import cancel_task
from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
from backend.application.workflows.external_doc_reconciliation import (
    EXTERNAL_DOC_RETRY_POLICY_VERSION, reconcile_external_doc_retry,
)
from backend.domain.tasks import DependencySpec, Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import CardAssetRepository, CardRepository, SourceRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository


def _seed_card(base_dir):
    cards = CardRepository(base_dir)
    cards.upsert_card({
        "card_id": "cancel_card", "source_id": None, "source_platform": "rss", "source_type": "article",
        "source_url": "https://example.com/cancel", "source_author": "Example", "title": "Cancel pipeline",
        "content": "Durable card content.", "captured_at": "2026-10-27T00:00:00Z",
        "processed_at": "2026-10-27T00:00:00Z", "raw_payload": {},
    })
    return cards.get_card("cancel_card")


@pytest.mark.parametrize("stage", [
    TaskType.CONTENT_ENRICH, TaskType.MEDIA_DOWNLOAD, TaskType.VIDEO_EXTRACT_AUDIO, TaskType.VIDEO_TRANSCRIBE,
])
def test_cancel_does_not_execute_or_reconcile_finalize_chain(tmp_path, stage):
    card = _seed_card(tmp_path)
    card_id = card["card_id"]
    repo = TaskRepository.for_base_dir(tmp_path)
    enrich_version = enrichment_input_version(card)
    enrich = Task(
        task_id=versioned_task_id(TaskType.CONTENT_ENRICH, card_id, enrich_version),
        task_type=TaskType.CONTENT_ENRICH, target_kind="card", target_id=card_id,
        input_version=enrich_version, status=TaskStatus.SUCCESS,
    )
    media_version = media_input_version(card=card, enrichment_version=enrich_version)
    media = Task(
        task_id=versioned_task_id(TaskType.MEDIA_DOWNLOAD, card_id, media_version),
        task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card", target_id=card_id,
        input_version=media_version, status=TaskStatus.SUCCESS,
    )
    tasks = [enrich] if stage == TaskType.CONTENT_ENRICH else [enrich, media]
    if stage in {TaskType.VIDEO_EXTRACT_AUDIO, TaskType.VIDEO_TRANSCRIBE}:
        assets = CardAssetRepository(tmp_path)
        assets.upsert_asset({
            "asset_id": "cancel_video", "card_id": card_id, "asset_kind": "video",
            "source_url": "https://example.com/video.mp4", "status": "downloaded",
        })
        asset = assets.get_asset("cancel_video")
        extract_version = asset_input_version(
            asset=asset, media_version=media_version, stage=TaskType.VIDEO_EXTRACT_AUDIO.value,
        )
        extract = Task(
            task_id=asset_task_id(TaskType.VIDEO_EXTRACT_AUDIO, asset["asset_id"], input_version=extract_version),
            task_type=TaskType.VIDEO_EXTRACT_AUDIO, target_kind="asset", target_id=asset["asset_id"],
            input_version=extract_version, status=TaskStatus.SUCCESS,
            artifacts_summary={"card_id": card_id},
        )
        tasks.append(extract)
        if stage == TaskType.VIDEO_TRANSCRIBE:
            tasks.append(Task(
                task_id="cancel_transcribe", task_type=stage, target_kind="asset", target_id=asset["asset_id"],
                depends_on=(DependencySpec(extract.task_id, TaskStatus.SUCCESS),),
                artifacts_summary={"card_id": card_id},
            ))
    cancelled = replace(tasks[-1], status=TaskStatus.BLOCKED)
    repo.enqueue_many([*tasks[:-1], cancelled])
    before_ids = repo.task_ids_for_card(card_id)

    assert cancel_task(tmp_path, cancelled.task_id, reason="取消这条处理链")["ok"] is True
    processed = run_tasks_by_ids(tmp_path, [cancelled.task_id], max_workers=1)
    repaired = reconcile_pipeline(tmp_path)

    assert processed["processed"] == 0
    assert repaired["created_by_type"] == {}
    assert repaired["errors"] == []
    assert repaired["terminal_cards_repaired"] == 0
    assert repo.task_ids_for_card(card_id) == before_ids
    assert CardRepository(tmp_path).get_card(card_id) == card


def test_cancel_fetch_does_not_update_source_or_circuit_or_create_run(tmp_path):
    sources = SourceRepository(tmp_path)
    sources.upsert_source({
        "source_id": "cancel_source", "platform": "rss", "source_type": "feed",
        "identifier": "https://example.com/rss", "display_name": "Cancelled source",
        "enabled": False, "connector_status": "supported", "cadence_hours": 24,
    })
    source = sources.get_source("cancel_source")
    circuits = RuntimeCircuitRepository(tmp_path)
    before_circuits = circuits.list_safe()
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="cancel_fetch", task_type=TaskType.FETCH, target_kind="source", target_id="cancel_source",
        status=TaskStatus.BLOCKED,
    ))

    assert cancel_task(tmp_path, "cancel_fetch", reason="来源已停用")["ok"] is True
    assert run_tasks_by_ids(tmp_path, ["cancel_fetch"], max_workers=1)["processed"] == 0
    assert sources.get_source("cancel_source") == source
    assert circuits.list_safe() == before_circuits
    with sources.connect_existing() as conn:
        assert conn.execute("select count(*) from run_history").fetchone()[0] == 0
        assert conn.execute("select count(*) from tasks").fetchone()[0] == 1


def test_cancelled_legacy_calibration_is_not_replaced_by_reconciliation(tmp_path):
    sources = SourceRepository(tmp_path)
    sources.upsert_source({
        "source_id": "cancel_initial", "platform": "rss", "source_type": "feed",
        "identifier": "https://example.com/rss", "display_name": "Initial source",
        "enabled": True, "connector_status": "implemented_uncalibrated", "cadence_hours": 24,
    })
    source = sources.get_source("cancel_initial")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="cancel_legacy", task_type=TaskType.SOURCE_CALIBRATE,
        target_kind="source", target_id=source["source_id"],
        input_version=source_calibration_input_version(source),
    ))

    assert cancel_task(tmp_path, "cancel_legacy", reason="无需初始化")["ok"] is True
    result = reconcile_pipeline(tmp_path)

    assert result["calibration_sources_scanned"] == 0
    assert result["created_by_type"] == {}
    assert [task.task_id for task in repo.list_by_target(target_kind="source", target_id=source["source_id"])] == ["cancel_legacy"]


def test_cancelled_external_document_retry_does_not_spawn_another_attempt(tmp_path):
    card = {**_seed_card(tmp_path), "source_platform": "zsxq"}
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="cancel_external", task_type=TaskType.CONTENT_ENRICH,
        target_kind="card", target_id=card["card_id"],
        artifacts_summary={"external_doc_retry_policy_version": EXTERNAL_DOC_RETRY_POLICY_VERSION},
    ))
    assert cancel_task(tmp_path, "cancel_external", reason="不再补读")["ok"] is True
    created = Counter()

    reconcile_external_doc_retry(repo, card, [{
        "block_kind": "external_doc", "status": "blocked",
        "raw_payload": {"block_reason": "external_doc_browser_unavailable"},
    }], created)

    assert created == {}
    assert repo.task_ids_for_card(card["card_id"]) == {"cancel_external"}
