from pathlib import Path
from datetime import datetime
from typing import Any
import pytest

from backend.application.tasks.finalization import TaskFinalizationQueue
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.transitions import enqueue_downstream
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository


def _setup_video(base_dir: Path, *, asset_status: str = "downloaded") -> TaskRepository:
    CardRepository(base_dir).upsert_card(
        {
            "card_id": "card_1", "source_platform": "youtube", "source_type": "video",
            "source_url": "https://example.test/video", "title": "video", "content": "video",
            "captured_at": "2026-07-12T00:00:00Z", "processed_at": "2026-07-12T00:00:00Z",
        }
    )
    CardAssetRepository(base_dir).upsert_asset(
        {
            "asset_id": "video_1",
            "card_id": "card_1",
            "asset_kind": "video",
            "source_url": "https://example.test/video.mp4",
            "status": asset_status,
        }
    )
    return TaskRepository.for_base_dir(base_dir)


def _enqueue(repo: TaskRepository, task: Task, status: TaskStatus) -> None:
    repo.enqueue(task)
    repo.update_status(task.task_id, status)


def _finalize(repo: TaskRepository) -> Task:
    tasks = repo.list_tasks(task_type=TaskType.CARD_FINALIZE)
    assert len(tasks) == 1
    return tasks[0]


def _transition(repo: TaskRepository, task: Task, status: TaskStatus, artifacts: dict[str, Any], base_dir: Path) -> None:
    with repo._connect() as connection:
        queue = TaskFinalizationQueue(connection, now=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        enqueue_downstream(queue, task, status=status, artifacts=artifacts, base_dir=base_dir)


def test_asr_branch_depends_on_linked_transcript_not_concatenated_extract(tmp_path: Path) -> None:
    repo = _setup_video(tmp_path)
    extract = Task(task_id="zz_extract", task_type=TaskType.VIDEO_EXTRACT_AUDIO, target_kind="asset", target_id="video_1")
    transcribe = Task(
        task_id="aa_transcribe", task_type=TaskType.VIDEO_TRANSCRIBE, target_kind="asset", target_id="video_1",
        depends_on=(DependencySpec(task_id=extract.task_id, required_status=TaskStatus.SUCCESS),),
        artifacts_summary={"card_id": "card_1"},
    )
    _enqueue(repo, extract, TaskStatus.SUCCESS)
    _enqueue(repo, transcribe, TaskStatus.SUCCESS)

    _transition(repo, transcribe, TaskStatus.SUCCESS, {"card_id": "card_1"}, tmp_path)

    finalize = _finalize(repo)
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": "aa_transcribe", "status": "success"}
    ]


def test_coexisting_asr_branches_choose_transcript_linked_to_current_extract(tmp_path: Path) -> None:
    repo = _setup_video(tmp_path)
    old_extract = Task(
        task_id="extract_1_old", task_type=TaskType.VIDEO_EXTRACT_AUDIO,
        target_kind="asset", target_id="video_1",
    )
    old_transcribe = Task(
        task_id="transcribe_1_old", task_type=TaskType.VIDEO_TRANSCRIBE,
        target_kind="asset", target_id="video_1",
        depends_on=(DependencySpec(task_id=old_extract.task_id, required_status=TaskStatus.SUCCESS),),
    )
    new_extract = Task(
        task_id="extract_2_new", task_type=TaskType.VIDEO_EXTRACT_AUDIO,
        target_kind="asset", target_id="video_1",
    )
    new_transcribe = Task(
        task_id="transcribe_2_new", task_type=TaskType.VIDEO_TRANSCRIBE,
        target_kind="asset", target_id="video_1",
        depends_on=(DependencySpec(task_id=new_extract.task_id, required_status=TaskStatus.SUCCESS),),
        artifacts_summary={"card_id": "card_1"},
    )
    for task in (old_extract, old_transcribe, new_extract, new_transcribe):
        _enqueue(repo, task, TaskStatus.SUCCESS)

    _transition(repo, new_transcribe, TaskStatus.SUCCESS, {"card_id": "card_1"}, tmp_path)

    finalize = _finalize(repo)
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": "transcribe_2_new", "status": "success"}
    ]


@pytest.mark.parametrize("terminal_status", [TaskStatus.FAILED, TaskStatus.BLOCKED])
def test_terminal_transcript_produces_transcript_dependency_and_gap(
    tmp_path: Path, terminal_status: TaskStatus
) -> None:
    repo = _setup_video(tmp_path)
    extract = Task(
        task_id="extract_for_terminal_transcript", task_type=TaskType.VIDEO_EXTRACT_AUDIO,
        target_kind="asset", target_id="video_1",
    )
    transcribe = Task(
        task_id=f"transcribe_{terminal_status.value}", task_type=TaskType.VIDEO_TRANSCRIBE,
        target_kind="asset", target_id="video_1",
        depends_on=(DependencySpec(task_id=extract.task_id, required_status=TaskStatus.SUCCESS),),
        artifacts_summary={"card_id": "card_1"},
    )
    _enqueue(repo, extract, TaskStatus.SUCCESS)
    _enqueue(repo, transcribe, terminal_status)

    _transition(
        repo, transcribe, terminal_status,
        {"card_id": "card_1", "reason": f"transcript_{terminal_status.value}"}, tmp_path,
    )

    finalize = _finalize(repo)
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": transcribe.task_id, "status": terminal_status.value}
    ]
    assert finalize.artifacts_summary["media_gaps"] == [{
        "missing_part": "video_transcript",
        "reason": f"transcript_{terminal_status.value}",
        "status": terminal_status.value,
        "task_id": transcribe.task_id,
    }]


def test_native_transcript_branch_depends_on_skipped_extract(tmp_path: Path) -> None:
    repo = _setup_video(tmp_path)
    extract = Task(
        task_id="extract_native", task_type=TaskType.VIDEO_EXTRACT_AUDIO, target_kind="asset", target_id="video_1",
        artifacts_summary={"card_id": "card_1"},
    )
    _enqueue(repo, extract, TaskStatus.SKIPPED)

    _transition(repo, extract, TaskStatus.SKIPPED, {"card_id": "card_1"}, tmp_path)

    finalize = _finalize(repo)
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": "extract_native", "status": "skipped"}
    ]


def test_failed_video_terminal_allows_partial_context_with_gap(tmp_path: Path) -> None:
    repo = _setup_video(tmp_path)
    extract = Task(
        task_id="extract_failed", task_type=TaskType.VIDEO_EXTRACT_AUDIO, target_kind="asset", target_id="video_1",
        artifacts_summary={"card_id": "card_1"},
    )
    _enqueue(repo, extract, TaskStatus.FAILED)

    _transition(repo, extract, TaskStatus.FAILED, {"card_id": "card_1", "reason": "decoder_failed"}, tmp_path)

    finalize = _finalize(repo)
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": "extract_failed", "status": "failed"}
    ]
    assert finalize.artifacts_summary["media_gaps"][0]["reason"] == "decoder_failed"


def test_successful_extract_without_audio_path_yields_partial_context_gap(tmp_path: Path) -> None:
    repo = _setup_video(tmp_path)
    extract = Task(
        task_id="extract_without_audio", task_type=TaskType.VIDEO_EXTRACT_AUDIO, target_kind="asset", target_id="video_1",
        artifacts_summary={"card_id": "card_1"},
    )
    _enqueue(repo, extract, TaskStatus.SUCCESS)

    _transition(repo, extract, TaskStatus.SUCCESS, {"card_id": "card_1"}, tmp_path)

    finalize = _finalize(repo)
    assert finalize.artifacts_summary["media_gaps"][0]["reason"] == "missing_audio_path"


def test_video_without_audio_stream_is_explicitly_not_applicable(tmp_path: Path) -> None:
    repo = _setup_video(tmp_path)
    extract = Task(
        task_id="extract_without_audio_stream",
        task_type=TaskType.VIDEO_EXTRACT_AUDIO,
        target_kind="asset",
        target_id="video_1",
        artifacts_summary={"card_id": "card_1"},
    )
    _enqueue(repo, extract, TaskStatus.SKIPPED)

    _transition(
        repo,
        extract,
        TaskStatus.SKIPPED,
        {
            "card_id": "card_1",
            "reason": "Video has no audio stream",
            "transcript_not_applicable": True,
        },
        tmp_path,
    )

    finalize = _finalize(repo)
    assert finalize.artifacts_summary["media_gaps"] == []


def test_skipped_no_media_expected_builds_complete_context_without_gap(tmp_path: Path) -> None:
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_no_media", "source_platform": "rss", "source_type": "feed",
            "source_url": "https://example.test/plain", "title": "plain", "content": "text only",
            "captured_at": "2026-07-12T00:00:00Z", "processed_at": "2026-07-12T00:00:00Z",
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    media = Task(
        task_id="media_no_expected", task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card", target_id="card_no_media",
    )
    _enqueue(repo, media, TaskStatus.SKIPPED)

    _transition(
        repo, media, TaskStatus.SKIPPED,
        {"card_id": "card_no_media", "download_status": "skipped_no_media_expected", "gap_reasons": []},
        tmp_path,
    )

    assert _finalize(repo).artifacts_summary["media_gaps"] == []


def test_usable_subtitle_current_chain_ignores_historical_media_tasks(tmp_path: Path) -> None:
    repo = _setup_video(tmp_path)
    old_extract = Task(
        task_id="old_extract_pending",
        task_type=TaskType.VIDEO_EXTRACT_AUDIO,
        target_kind="asset",
        target_id="video_1",
        artifacts_summary={"card_id": "card_1"},
    )
    old_transcribe = Task(
        task_id="old_transcribe_pending",
        task_type=TaskType.VIDEO_TRANSCRIBE,
        target_kind="asset",
        target_id="video_1",
        depends_on=(DependencySpec(task_id=old_extract.task_id, required_status=TaskStatus.SUCCESS),),
    )
    current_media = Task(
        task_id="media_current_subtitle",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card_1",
        artifacts_summary={"card_id": "card_1", "download_status": "skipped_transcript_available", "assets": []},
    )
    repo.enqueue(old_extract)
    repo.enqueue(old_transcribe)
    _enqueue(repo, current_media, TaskStatus.SUCCESS)

    _transition(
        repo,
        current_media,
        TaskStatus.SUCCESS,
        {"card_id": "card_1", "download_status": "skipped_transcript_available", "assets": []},
        tmp_path,
    )

    finalize = _finalize(repo)
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": current_media.task_id, "status": "success"}
    ]
    assert [task.task_id for task in repo.list_tasks(task_type=TaskType.VIDEO_EXTRACT_AUDIO)] == [old_extract.task_id]
    assert [task.task_id for task in repo.list_tasks(task_type=TaskType.VIDEO_TRANSCRIBE)] == [old_transcribe.task_id]

    repo.update_status(finalize.task_id, TaskStatus.SUCCESS)
    _transition(repo, finalize, TaskStatus.SUCCESS, {}, tmp_path)
    assert repo.list_tasks(task_type=TaskType.SUMMARY) == []
