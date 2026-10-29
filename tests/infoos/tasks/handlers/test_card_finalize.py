from __future__ import annotations

from pathlib import Path

import pytest

from backend.application.tasks.handlers.card_finalize import handle_card_finalize
from backend.application.tasks.processor import process_task
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
)


def _seed_card(base_dir: Path, card_id: str) -> None:
    CardRepository(base_dir).upsert_card(
        {
            "card_id": card_id,
            "card_type": "information",
            "source_id": None,
            "source_platform": "xiaohongshu",
            "source_type": "social_post",
            "source_url": f"https://www.xiaohongshu.com/explore/{card_id}",
            "source_author": "Example",
            "title": "A collected note",
            "content": "The readable body.",
            "summary": "",
            "captured_at": "2026-07-16T00:00:00Z",
            "processed_at": "2026-07-16T00:01:00Z",
            "processor_version": "information-card-builder-v1",
            "raw_connector_id": "xhs_example",
            "raw_item_id": "note-1",
            "source_run_id": "run-1",
            "raw_payload": {},
        }
    )


def _finalize_task(card_id: str, *, artifacts: dict | None = None, attempt: int = 1) -> Task:
    return Task(
        task_id=f"card_finalize_{card_id}_{attempt}",
        task_type=TaskType.CARD_FINALIZE,
        target_kind="card",
        target_id=card_id,
        attempt=attempt,
        artifacts_summary=artifacts or {},
    )


def test_card_finalize_marks_a_card_without_missing_media_complete(tmp_path: Path) -> None:
    _seed_card(tmp_path, "card_complete")
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "body_complete",
            "card_id": "card_complete",
            "position": 0,
            "layer": "source",
            "block_kind": "article_body",
            "status": "enriched",
            "body": "The readable body.",
            "segments": [],
            "provenance": "source",
            "raw_payload": {},
        }
    )

    result = handle_card_finalize(_finalize_task("card_complete"), tmp_path)

    assert result["status"] == TaskStatus.SUCCESS.value
    assert result["artifacts"]["completeness_status"] == "complete"
    assert result["artifacts"]["version"] == 1
    assert result["artifacts"]["content_hash"].startswith("sha256:")
    assert result["artifacts"]["changed"] is True
    stored = CardRepository(tmp_path).get_card("card_complete")
    assert stored["status"] == "active"
    assert stored["missing_reasons"] == []


def test_card_finalize_keeps_media_failures_as_partial_delivery_evidence(tmp_path: Path) -> None:
    _seed_card(tmp_path, "card_partial")
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "gap_partial",
            "card_id": "card_partial",
            "position": 2,
            "layer": "derived",
            "block_kind": "media_download_gap",
            "status": "blocked",
            "body": "Video unavailable",
            "segments": [],
            "provenance": "media downloader",
            "raw_payload": {"reason": "yt_dlp_download_failed"},
        }
    )
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video_partial",
            "card_id": "card_partial",
            "asset_kind": "video",
            "source_url": "https://media.example/video.mp4",
            "status": "failed",
            "raw_payload": {"reason": "remote_forbidden"},
        }
    )
    upstream = Task(
        task_id="video_download_partial",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card_partial",
        status=TaskStatus.FAILED,
        error_reason="download_failed",
        artifacts_summary={"failure_reason": "provider_forbidden"},
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(upstream)

    result = handle_card_finalize(
        _finalize_task(
            "card_partial",
            artifacts={
                "upstream_media_tasks": [
                    {"task_id": upstream.task_id, "status": TaskStatus.FAILED.value}
                ]
            },
        ),
        tmp_path,
    )

    assert result["status"] == TaskStatus.SUCCESS.value
    assert result["artifacts"]["completeness_status"] == "partial"
    stored = CardRepository(tmp_path).get_card("card_partial")
    assert "yt_dlp_download_failed" in stored["missing_reasons"]
    assert "remote_forbidden" in stored["missing_reasons"]
    assert "provider_forbidden" in stored["missing_reasons"]


def test_card_finalize_marks_unfetched_external_document_as_partial(tmp_path: Path) -> None:
    _seed_card(tmp_path, "card_external_doc_pending")
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "external_doc_pending",
            "card_id": "card_external_doc_pending",
            "position": 1,
            "layer": "external",
            "block_kind": "external_doc",
            "status": "pending",
            "body": "检测到外部长文链接。",
            "raw_payload": {"block_reason": "external_doc_browser_unavailable"},
        }
    )

    result = handle_card_finalize(
        _finalize_task("card_external_doc_pending"),
        tmp_path,
    )

    assert result["artifacts"]["completeness_status"] == "partial"
    assert CardRepository(tmp_path).get_card("card_external_doc_pending")[
        "missing_reasons"
    ] == ["external_doc_browser_unavailable"]


def test_card_finalize_accepts_fetched_external_document_as_complete(tmp_path: Path) -> None:
    _seed_card(tmp_path, "card_external_doc_fetched")
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "external_doc_fetched",
            "card_id": "card_external_doc_fetched",
            "position": 1,
            "layer": "external",
            "block_kind": "external_doc",
            "status": "body_fetched",
            "body": "已经抓取的完整外部长文正文。",
            "raw_payload": {"fetch_state": "fetched"},
        }
    )

    result = handle_card_finalize(
        _finalize_task("card_external_doc_fetched"),
        tmp_path,
    )

    assert result["artifacts"]["completeness_status"] == "complete"


def test_card_finalize_treats_understood_media_and_no_media_expected_as_complete(tmp_path: Path) -> None:
    _seed_card(tmp_path, "card_delivered_media")
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video_understood",
            "card_id": "card_delivered_media",
            "asset_kind": "video",
            "source_url": "https://media.example/video.mp4",
            "status": "understood",
            "raw_payload": {
                "transcription_status": "enriched",
                "understanding_status": "enriched",
            },
        }
    )
    upstream = Task(
        task_id="media_download_no_media_expected",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card_delivered_media",
        status=TaskStatus.SKIPPED,
        artifacts_summary={"download_status": "skipped_no_media_expected"},
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(upstream)

    result = handle_card_finalize(
        _finalize_task(
            "card_delivered_media",
            artifacts={
                "media_gaps": [{"reason": "skipped_no_media_expected"}],
                "upstream_media_tasks": [
                    {"task_id": upstream.task_id, "status": TaskStatus.SKIPPED.value}
                ],
            },
        ),
        tmp_path,
    )

    assert result["artifacts"]["completeness_status"] == "complete"
    assert CardRepository(tmp_path).get_card("card_delivered_media")["missing_reasons"] == []


@pytest.mark.parametrize(
    ("candidate_count", "expected_status"),
    [(0, "complete"), (1, "partial")],
)
def test_card_finalize_only_resolves_explicit_x_no_media_when_no_candidates(
    tmp_path: Path,
    candidate_count: int,
    expected_status: str,
) -> None:
    card_id = f"card_x_no_media_{candidate_count}"
    _seed_card(tmp_path, card_id)
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("update cards set source_platform = 'x' where card_id = ?", (card_id,))
    upstream = Task(
        task_id=f"media_x_no_media_{candidate_count}",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id=card_id,
        status=TaskStatus.SKIPPED,
        artifacts_summary={
            "download_status": "skipped_no_media_found",
            "candidate_count": candidate_count,
            "gap_reasons": ["x_media_download_no_media_found"],
        },
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(upstream)

    handle_card_finalize(
        _finalize_task(
            card_id,
            artifacts={
                "upstream_media_tasks": [
                    {"task_id": upstream.task_id, "status": TaskStatus.SKIPPED.value}
                ]
            },
        ),
        tmp_path,
    )

    stored = CardRepository(tmp_path).get_card(card_id)
    assert stored["completeness_status"] == expected_status
    assert stored["missing_reasons"] == (
        [] if candidate_count == 0 else ["skipped_no_media_found"]
    )


def test_card_finalize_does_not_publish_stale_no_media_artifacts_after_retry(
    tmp_path: Path,
) -> None:
    card_id = "card_x_no_media_retrying"
    _seed_card(tmp_path, card_id)
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("update cards set source_platform = 'x' where card_id = ?", (card_id,))
    upstream = Task(
        task_id="media_x_no_media_retrying",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id=card_id,
        status=TaskStatus.SKIPPED,
        artifacts_summary={
            "download_status": "skipped_no_media_found",
            "candidate_count": 0,
            "gap_reasons": ["x_media_download_no_media_found"],
        },
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(upstream)
    assert repo.retry(upstream.task_id) is True

    result = handle_card_finalize(
        _finalize_task(
            card_id,
            artifacts={
                "upstream_media_tasks": [
                    {"task_id": upstream.task_id, "status": TaskStatus.SKIPPED.value}
                ]
            },
        ),
        tmp_path,
    )

    assert result["status"] == TaskStatus.PENDING.value
    assert result["artifacts"]["reason"] == "upstream_media_not_terminal"
    stored = CardRepository(tmp_path).get_card(card_id)
    assert stored["status"] == "processing"
    assert stored["completeness_status"] == "processing"


def test_finalize_race_defers_without_attempt_cost_and_completes_after_media_terminal(
    tmp_path: Path,
) -> None:
    card_id = "card_x_no_media_race"
    _seed_card(tmp_path, card_id)
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("update cards set source_platform = 'x' where card_id = ?", (card_id,))
    upstream = Task(
        task_id="media_x_no_media_race",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id=card_id,
        status=TaskStatus.SKIPPED,
        artifacts_summary={
            "download_status": "skipped_no_media_found",
            "candidate_count": 0,
            "gap_reasons": ["x_media_download_no_media_found"],
        },
    )
    finalize = Task(
        task_id="card_finalize_x_no_media_race",
        task_type=TaskType.CARD_FINALIZE,
        target_kind="card",
        target_id=card_id,
        depends_on=(DependencySpec(upstream.task_id, TaskStatus.SKIPPED),),
        artifacts_summary={
            "upstream_media_tasks": [
                {"task_id": upstream.task_id, "status": TaskStatus.SKIPPED.value}
            ]
        },
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(upstream)
    repo.enqueue(finalize)
    claimed = repo.claim_task_ids(
        [finalize.task_id],
        worker_id="worker-race",
        now="2026-07-22T00:00:00Z",
        lease_expires_at="2026-07-22T00:01:00Z",
        task_types=(TaskType.CARD_FINALIZE,),
    )
    assert [item.task_id for item in claimed] == [finalize.task_id]
    assert repo.retry(upstream.task_id) is True

    deferred = process_task(
        claimed[0],
        tmp_path,
        now="2026-07-22T00:00:00Z",
        worker_id="worker-race",
        lease_seconds=60,
    )

    assert deferred["status"] == TaskStatus.PENDING.value
    waiting = repo.get(finalize.task_id)
    assert waiting is not None and waiting.status == TaskStatus.PENDING
    assert waiting.attempt == 1
    assert CardRepository(tmp_path).get_card(card_id)["status"] == "processing"

    repo.update_status(upstream.task_id, TaskStatus.SKIPPED)
    reclaimed = repo.claim_task_ids(
        [finalize.task_id],
        worker_id="worker-race-2",
        now="2026-07-22T00:00:02Z",
        lease_expires_at="2026-07-22T00:01:02Z",
        task_types=(TaskType.CARD_FINALIZE,),
    )
    assert [item.task_id for item in reclaimed] == [finalize.task_id]
    completed = process_task(
        reclaimed[0],
        tmp_path,
        now="2026-07-22T00:00:02Z",
        worker_id="worker-race-2",
        lease_seconds=60,
    )

    assert completed["status"] == TaskStatus.SUCCESS.value
    stored = CardRepository(tmp_path).get_card(card_id)
    assert stored["status"] == "active"
    assert stored["completeness_status"] == "complete"
    assert stored["missing_reasons"] == []


def test_card_finalize_keeps_optional_media_intelligence_failure_out_of_completeness(
    tmp_path: Path,
) -> None:
    task_type = TaskType.IMAGE_UNDERSTAND
    _seed_card(tmp_path, "card_optional_image_ai")
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "image_delivered",
            "card_id": "card_optional_image_ai",
            "asset_kind": "image",
            "source_url": "https://media.example/image.jpg",
            "status": "downloaded",
            "raw_payload": {
                "understanding_status": "failed",
                "understanding_error": "invalid optional model response",
            },
        }
    )
    upstream = Task(
        task_id=f"{task_type.value.lower()}_optional",
        task_type=task_type,
        target_kind="asset",
        target_id="image_delivered",
        status=TaskStatus.FAILED,
        error_reason="invalid optional model response",
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(upstream)

    result = handle_card_finalize(
        _finalize_task(
            "card_optional_image_ai",
            artifacts={
                "media_gaps": [
                    {
                        "missing_part": {
                            TaskType.IMAGE_UNDERSTAND: "image_understanding",
                            TaskType.VIDEO_EXTRACT_AUDIO: "audio_extract",
                            TaskType.VIDEO_TRANSCRIBE: "video_transcript",
                        }[task_type],
                        "reason": "optional_intelligence_failed",
                        "status": TaskStatus.FAILED.value,
                        "task_id": upstream.task_id,
                    }
                ],
                "upstream_media_tasks": [
                    {"task_id": upstream.task_id, "status": TaskStatus.FAILED.value}
                ]
            },
        ),
        tmp_path,
    )

    assert result["artifacts"]["completeness_status"] == "complete"
    assert CardRepository(tmp_path).get_card("card_optional_image_ai")["missing_reasons"] == []


@pytest.mark.parametrize(
    ("task_type", "missing_part"),
    [
        (TaskType.VIDEO_EXTRACT_AUDIO, "audio_extract"),
        (TaskType.VIDEO_TRANSCRIBE, "video_transcript"),
    ],
)
def test_card_finalize_marks_missing_video_transcript_facts_partial(
    tmp_path: Path,
    task_type: TaskType,
    missing_part: str,
) -> None:
    card_id = f"card_required_{missing_part}"
    _seed_card(tmp_path, card_id)
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": f"asset_{missing_part}",
            "card_id": card_id,
            "asset_kind": "video",
            "source_url": "https://media.example/video.mp4",
            "status": "downloaded",
        }
    )
    upstream = Task(
        task_id=f"{task_type.value.lower()}_required",
        task_type=task_type,
        target_kind="asset",
        target_id=f"asset_{missing_part}",
        status=TaskStatus.BLOCKED,
        error_reason="asr_runtime_unavailable",
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(upstream)

    result = handle_card_finalize(
        _finalize_task(
            card_id,
            artifacts={
                "media_gaps": [
                    {
                        "missing_part": missing_part,
                        "reason": "asr_runtime_unavailable",
                        "status": TaskStatus.BLOCKED.value,
                        "task_id": upstream.task_id,
                    }
                ],
                "upstream_media_tasks": [
                    {"task_id": upstream.task_id, "status": TaskStatus.BLOCKED.value}
                ],
            },
        ),
        tmp_path,
    )

    assert result["artifacts"]["completeness_status"] == "partial"
    assert CardRepository(tmp_path).get_card(card_id)["missing_reasons"] == [
        "asr_runtime_unavailable"
    ]


def test_card_finalize_ignores_attempt_and_runtime_noise_when_versioning(tmp_path: Path) -> None:
    _seed_card(tmp_path, "card_stable")

    first = handle_card_finalize(
        _finalize_task(
            "card_stable",
            artifacts={"attempt_log": "first", "finished_at": "2026-07-16T00:02:00Z"},
            attempt=1,
        ),
        tmp_path,
    )
    second = handle_card_finalize(
        _finalize_task(
            "card_stable",
            artifacts={"attempt_log": "retry", "finished_at": "2026-07-16T04:05:00Z"},
            attempt=3,
        ),
        tmp_path,
    )

    assert first["artifacts"]["version"] == second["artifacts"]["version"] == 1
    assert first["artifacts"]["content_hash"] == second["artifacts"]["content_hash"]
    assert second["artifacts"]["changed"] is False
