from __future__ import annotations

from pathlib import Path

from tests.infoos._legacy_history import seed_legacy_insight, seed_legacy_row

from backend.infrastructure.storage.repositories import CardAssetRepository, CardRepository, SourceRepository
from backend.infrastructure.observability.logging import resolve_log_path
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.batches import create_task_batch, get_task_batch
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks, run_tasks_by_ids


def _card_payload(card_id: str) -> dict[str, object]:
    return {
        "card_id": card_id,
        "source_id": None,
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": f"https://x.com/example/status/{card_id}",
        "source_author": "Example",
        "title": "Durable pipeline card",
        "content": "A card with enough durable content for the pipeline.",
        "captured_at": "2026-07-10T00:00:00Z",
        "processed_at": "2026-07-10T00:00:00Z",
        "raw_payload": {},
    }


def _insight_payload(input_version: str) -> dict[str, object]:
    return {
        "main_point": "summary",
        "topic_assignment": None,
        "scores": {},
        "score_reasons": {},
        "why_it_matters": "test",
        "is_time_sensitive": False,
        "evidence_quotes": [],
        "model": "deterministic",
        "prompt_version": "summary-v1",
        "input_version": input_version,
        "generated_at": "2026-07-10T00:00:00Z",
    }


def _one_task(repo: TaskRepository, task_type: TaskType) -> Task:
    tasks = repo.list_tasks(task_type=task_type, limit=10)
    assert len(tasks) == 1
    return tasks[0]


def test_new_card_pipeline_does_not_schedule_legacy_intelligence_stages(tmp_path: Path):
    """The collector product ends at an information card, not downstream knowledge work."""
    card_id = "collector_terminal_card"
    CardRepository(tmp_path).upsert_card(_card_payload(card_id))
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_collector_terminal_card",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id=card_id,
            input_version="media-v1",
        )
    )

    result = run_due_tasks(
        tmp_path,
        handlers={
            TaskType.MEDIA_DOWNLOAD: lambda _task, _base_dir: {
                "status": "success",
                "artifacts": {"download_status": "skipped_no_media_expected"},
            }
        },
    )

    assert result["succeeded"] == 1
    assert len(repo.list_tasks(task_type=TaskType.CARD_FINALIZE, limit=10)) == 1
    for legacy_type in (
        TaskType.CONTEXT_PACK,
        TaskType.SUMMARY,
        TaskType.SEMANTIC_EXTRACT,
        TaskType.SCORE,
        TaskType.DECIDE,
        TaskType.OUTPUT_GENERATE,
        TaskType.TOPIC_UPDATE,
        TaskType.CLUSTER,
    ):
        assert repo.list_tasks(task_type=legacy_type, limit=10) == []


def test_successful_legacy_topic_update_does_not_extend_collector_chain(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="topic_update_card_1_v1",
            task_type=TaskType.TOPIC_UPDATE,
            target_kind="card",
            target_id="card_1",
            input_version="output-v1",
        )
    )

    result = run_due_tasks(
        tmp_path,
        handlers={TaskType.TOPIC_UPDATE: lambda _task, _base_dir: {"status": "success", "artifacts": {}}},
    )

    assert result["succeeded"] == 1
    assert repo.list_tasks(task_type=TaskType.CLUSTER, limit=10) == []


def test_summary_is_not_enqueued_before_context_block_exists(tmp_path: Path):
    CardRepository(tmp_path).upsert_card(_card_payload("card_without_context"))
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="context_pack_card_without_context",
            task_type=TaskType.CONTEXT_PACK,
            target_kind="card",
            target_id="card_without_context",
        )
    )

    result = run_due_tasks(
        tmp_path,
        handlers={
            TaskType.CONTEXT_PACK: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"context_block_id": "missing_context_block"},
            }
        },
    )

    assert result["succeeded"] == 1
    assert repo.list_tasks(task_type=TaskType.SUMMARY, limit=10) == []


def test_semantic_is_not_enqueued_before_summary_row_exists(tmp_path: Path):
    CardRepository(tmp_path).upsert_card(_card_payload("card_without_summary"))
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="summary_card_without_summary",
            task_type=TaskType.SUMMARY,
            target_kind="card",
            target_id="card_without_summary",
            input_version="summary-input-v1",
        )
    )

    result = run_due_tasks(
        tmp_path,
        handlers={TaskType.SUMMARY: lambda task, base_dir: {"status": "success", "artifacts": {}}},
    )

    assert result["succeeded"] == 1
    assert repo.list_tasks(task_type=TaskType.SEMANTIC_EXTRACT, limit=10) == []


def test_successful_legacy_semantic_task_does_not_enqueue_score(tmp_path: Path):
    from backend.infrastructure.storage.repositories import UserActionRepository

    card_id = "card_exact_annotation"
    CardRepository(tmp_path).upsert_card(_card_payload(card_id))
    UserActionRepository(tmp_path).upsert_action(
        {
            "action_id": "feedback_score_transition",
            "card_id": card_id,
            "action": "saved",
            "created_at": "2026-07-10T00:00:00Z",
            "updated_at": "2026-07-10T00:00:00Z",
            "details": {},
        }
    )
    seed_legacy_insight(tmp_path, card_id, _insight_payload("summary-v1"))
    seed_legacy_row(tmp_path, "semantic_annotations",
        {
            "annotation_id": "annotation_exact",
            "target_kind": "card",
            "target_id": card_id,
            "labels": [],
            "entities": [],
            "insight": "annotation",
            "confidence": 1,
            "evidence_refs": [],
            "model": "deterministic",
            "prompt_version": "semantic-v1",
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="semantic_extract_card_exact_annotation_upstream",
            task_type=TaskType.SEMANTIC_EXTRACT,
            target_kind="card",
            target_id=card_id,
            input_version="semantic-input-v1",
        )
    )

    run_due_tasks(
        tmp_path,
        handlers={
            TaskType.SEMANTIC_EXTRACT: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"annotation_id": "annotation_exact", "result_version": task.input_version},
            }
        },
    )

    assert repo.list_tasks(task_type=TaskType.SCORE, limit=10) == []


def test_successful_legacy_score_task_does_not_enqueue_decision_or_output(tmp_path: Path):
    card_id = "card_exact_products"
    CardRepository(tmp_path).upsert_card(_card_payload(card_id))
    seed_legacy_insight(tmp_path, card_id, _insight_payload("summary-v1"))
    seed_legacy_row(tmp_path, "semantic_annotations",
        {"annotation_id": "annotation_exact", "target_kind": "card", "target_id": card_id,
         "labels": [], "entities": [], "insight": "annotation", "confidence": 1,
         "evidence_refs": [], "model": "deterministic", "prompt_version": "semantic-v1"}
    )
    seed_legacy_row(tmp_path, "value_scores",
        {"score_id": "score_exact", "target_kind": "card", "target_id": card_id,
         "scores": {}, "score_reasons": {}}
    )
    seed_legacy_row(tmp_path, "decision_records",
        {"decision_id": "decision_exact", "target_kind": "card", "target_id": card_id,
         "route": "ignore", "reason": "test", "evidence_refs": [], "status": "ignored"}
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="score_card_exact_products_upstream",
            task_type=TaskType.SCORE,
            target_kind="card",
            target_id=card_id,
            input_version="score-input-v1",
            artifacts_summary={"annotation_id": "annotation_exact"},
        )
    )
    run_due_tasks(
        tmp_path,
        handlers={TaskType.SCORE: lambda task, base_dir: {"status": "success", "artifacts": {"score_id": "score_exact"}}},
    )
    assert repo.list_tasks(task_type=TaskType.DECIDE, limit=10) == []
    assert repo.list_tasks(task_type=TaskType.OUTPUT_GENERATE, limit=10) == []


def test_fetch_success_enqueues_card_build_per_raw_item(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    for raw_item_id in ("raw_1", "raw_2"):
        repo.enqueue(
            Task(
                task_id=f"card_build_{raw_item_id}",
                task_type=TaskType.CARD_BUILD,
                target_kind="raw_item",
                target_id=raw_item_id,
            )
        )
    repo.enqueue(
        Task(
            task_id="fetch_rss_1",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="rss_1",
            next_run_at="2026-06-23T00:00:00Z",
        )
    )

    result = run_due_tasks(
        tmp_path,
        now="2026-06-23T00:01:00Z",
        task_types=[TaskType.FETCH],
        handlers={
            TaskType.FETCH: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"card_build_task_ids": ["card_build_raw_1", "card_build_raw_2"]},
            }
        },
    )

    assert result["succeeded"] == 1
    assert repo.get("fetch_rss_1").status == TaskStatus.SUCCESS
    assert result["results"][0]["downstream_task_ids"] == ["card_build_raw_1", "card_build_raw_2"]
    assert repo.get("content_enrich_card_1") is None
    lines = resolve_log_path(tmp_path).read_text(encoding="utf-8").splitlines()[-20:]
    assert any("task_completed task_id=fetch_rss_1" in line for line in lines)


def test_card_build_success_enqueues_content_enrich_for_created_card(tmp_path: Path):
    CardRepository(tmp_path).upsert_card(_card_payload("card_1"))
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="card_build_raw_1",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id="raw_1",
            next_run_at="2026-06-23T00:00:00Z",
            artifacts_summary={
                "chain_id": "source_run_rss_1_20260623_000000",
                "chain_root_task_id": "fetch_rss_1",
            },
        )
    )

    run_due_tasks(
        tmp_path,
        now="2026-06-23T00:01:00Z",
        handlers={
            TaskType.CARD_BUILD: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"card_id": "card_1", "raw_item_id": "raw_1"},
            }
        },
    )

    enrich = _one_task(repo, TaskType.CONTENT_ENRICH)
    assert enrich.artifacts_summary["chain_id"] == "source_run_rss_1_20260623_000000"
    assert enrich.artifacts_summary["chain_root_task_id"] == "fetch_rss_1"
    assert enrich.artifacts_summary["upstream_task_id"] == "card_build_raw_1"


def test_child_task_attaches_to_parent_batches(tmp_path: Path):
    CardRepository(tmp_path).upsert_card(_card_payload("card_1"))
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="card_build_raw_1",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id="raw_1",
            next_run_at="2026-06-23T00:00:00Z",
        )
    )
    batch = create_task_batch(
        tmp_path,
        batch_type="source_run",
        task_ids=["card_build_raw_1"],
    )

    result = run_due_tasks(
        tmp_path,
        now="2026-06-23T00:01:00Z",
        handlers={
            TaskType.CARD_BUILD: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"card_id": "card_1", "raw_item_id": "raw_1"},
            }
        },
    )

    loaded = get_task_batch(tmp_path, batch["batch_id"])

    child = _one_task(repo, TaskType.CONTENT_ENRICH)
    assert result["results"][0]["downstream_task_ids"] == [child.task_id]
    assert repo.get("card_build_raw_1").artifacts_summary["downstream_task_ids"] == [child.task_id]
    assert loaded["task_ids"] == ["card_build_raw_1", child.task_id]


def test_fetch_success_does_not_backfill_card_enrichment_for_existing_card(tmp_path: Path, monkeypatch):
    repo = TaskRepository.for_base_dir(tmp_path)
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "source_youtube",
            "platform": "youtube",
            "source_type": "channel",
            "identifier": "AndrejKarpathy",
            "display_name": "Andrej Karpathy",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "source_youtube",
        }
    )
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_existing",
            "source_id": "source_youtube",
            "connector_id": "source_youtube",
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://www.youtube.com/watch?v=demoYt00001",
            "source_author": "Andrej Karpathy",
            "title": "",
            "content": "video",
            "captured_at": "2026-06-30T00:00:00Z",
            "processed_at": "2026-06-30T00:00:00Z",
            "raw_payload": {},
        }
    )
    repo.enqueue(
        Task(
            task_id="fetch_source_youtube",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_youtube",
            next_run_at="2026-06-23T00:00:00Z",
        )
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **_kwargs: {"status": "ready", "browser_headless": False},
    )

    result = run_due_tasks(
        tmp_path,
        now="2026-06-23T00:01:00Z",
        handlers={
            TaskType.FETCH: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"created_or_updated": "updated"},
            }
        },
    )

    assert result["succeeded"] == 1
    assert repo.get("content_enrich_card_existing") is None


def test_manual_fetch_task_does_not_enqueue_followup_fetch_tasks(tmp_path: Path):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_1",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "RSS 1",
            "cadence_hours": 0.5,
            "enabled": True,
            "connector_status": "supported",
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="fetch_rss_1_manual",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="rss_1",
            next_run_at="2026-06-23T00:00:00Z",
            artifacts_summary={"plan_followup_fetch": False},
        )
    )

    result = run_tasks_by_ids(
        tmp_path,
        ["fetch_rss_1_manual"],
        now="2026-06-23T00:01:00Z",
        handlers={
            TaskType.FETCH: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"created_or_updated": "updated"},
            }
        },
    )

    assert result["succeeded"] == 1
    assert repo.get("fetch_rss_1_manual").status == TaskStatus.SUCCESS
    assert repo.list_tasks(task_type=TaskType.FETCH, status=TaskStatus.PENDING) == []


def test_media_download_enqueues_image_and_video_subtasks(tmp_path: Path):
    CardRepository(tmp_path).upsert_card(_card_payload("card_1"))
    CardAssetRepository(tmp_path).upsert_asset(
        {"asset_id": "image_1", "card_id": "card_1", "asset_kind": "image", "source_url": "https://x.com/example/1", "status": "downloaded"}
    )
    CardAssetRepository(tmp_path).upsert_asset(
        {"asset_id": "video_1", "card_id": "card_1", "asset_kind": "video", "source_url": "https://x.com/example/1", "status": "downloaded"}
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_download_card_1",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_1",
        )
    )

    run_due_tasks(
        tmp_path,
        handlers={
            TaskType.MEDIA_DOWNLOAD: lambda task, base_dir: {
                "status": "success",
                "artifacts": {
                    "card_id": "card_1",
                    "assets": [
                        {"asset_id": "image_1", "asset_kind": "image"},
                        {"asset_id": "video_1", "asset_kind": "video"},
                    ],
                },
            }
        },
    )

    assert repo.list_tasks(task_type=TaskType.IMAGE_UNDERSTAND) == []
    video = _one_task(repo, TaskType.VIDEO_EXTRACT_AUDIO)
    assert video is not None
    assert video.target_kind == "asset"


def test_media_download_without_assets_enqueues_card_finalize(tmp_path: Path):
    CardRepository(tmp_path).upsert_card(_card_payload("card_1"))
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_download_card_1",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_1",
        )
    )

    run_due_tasks(
        tmp_path,
        handlers={
            TaskType.MEDIA_DOWNLOAD: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"card_id": "card_1", "assets": []},
            }
        },
    )

    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize is not None
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": "media_download_card_1", "status": "success"}
    ]


def test_successful_legacy_summary_does_not_enqueue_semantic_task(tmp_path: Path):
    CardRepository(tmp_path).upsert_card(_card_payload("card_1"))
    seed_legacy_insight(tmp_path, "card_1", _insight_payload("v1"))
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="summary_card_1",
            task_type=TaskType.SUMMARY,
            target_kind="card",
            target_id="card_1",
        )
    )

    run_due_tasks(
        tmp_path,
        handlers={
            TaskType.SUMMARY: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"result_version": "v1"},
            }
        },
    )

    assert repo.list_tasks(task_type=TaskType.SEMANTIC_EXTRACT, limit=10) == []


def test_failed_asset_without_media_task_does_not_block_card_finalize(tmp_path: Path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_1",
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/elonmusk/status/1",
            "source_author": "Elon Musk",
            "title": "post",
            "content": "post",
            "captured_at": "2026-06-23T00:00:00Z",
            "processed_at": "2026-06-23T00:00:00Z",
        }
    )
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "image_failed",
            "card_id": "card_1",
            "asset_kind": "image",
            "source_url": "https://x.com/elonmusk/status/1",
            "local_path": "card_1/image.jpg",
            "status": "failed",
        }
    )
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "image_done",
            "card_id": "card_1",
            "asset_kind": "image",
            "source_url": "https://x.com/elonmusk/status/1",
            "local_path": "card_1/done.jpg",
            "status": "skipped",
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_download_card_1",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_1",
        )
    )

    run_due_tasks(
        tmp_path,
        handlers={
            TaskType.MEDIA_DOWNLOAD: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"card_id": "card_1", "assets": []},
            }
        },
    )

    assert _one_task(repo, TaskType.CARD_FINALIZE) is not None
