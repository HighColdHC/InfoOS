from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks, run_tasks_by_ids


@pytest.fixture(autouse=True)
def _ready_browser_lane(monkeypatch):
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **_kwargs: {
            "status": "ready",
            "browser_headless": False,
            "profile": {
                "alias": "infoos-fetch",
                "bound": True,
                "binding_status": "ready",
            },
        },
    )


def _one_task(repo: TaskRepository, task_type: TaskType) -> Task:
    tasks = repo.list_tasks(task_type=task_type, limit=10)
    assert len(tasks) == 1
    return tasks[0]


def test_reddit_text_post_skips_media_without_recording_a_gap(tmp_path: Path, monkeypatch):
    from backend.application.tasks.handlers.media_download import handle_media_download

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_reddit_text",
            "source_id": None,
            "source_platform": "reddit",
            "source_type": "social_post",
            "source_url": "https://www.reddit.com/r/OpenAI/comments/example/text_post/",
            "source_author": "Example",
            "title": "Text post",
            "content": "A complete text-only Reddit post.",
            "captured_at": "2026-07-19T00:00:00Z",
            "processed_at": "2026-07-19T00:00:00Z",
            "raw_payload": {"selftext": "A complete text-only Reddit post."},
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "gap_reddit_text",
            "card_id": "card_reddit_text",
            "position": 1,
            "layer": "derived",
            "block_kind": "media_download_gap",
            "status": "blocked",
            "body": "Historical gallery authentication gap",
            "source_url": "https://www.reddit.com/r/OpenAI/comments/example/text_post/",
            "provenance": "test",
            "raw_payload": {"reason": "auth_required"},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.download_card_media",
        lambda *_args, **_kwargs: [],
    )

    result = handle_media_download(
        Task("media_reddit_text", TaskType.MEDIA_DOWNLOAD, "card", "card_reddit_text"),
        tmp_path,
    )

    assert result["status"] == TaskStatus.SKIPPED.value
    assert result["artifacts"]["download_status"] == "skipped_no_media_expected"
    assert result["artifacts"]["gap_reasons"] == []
    gap = next(
        block
        for block in CardContentBlockRepository(tmp_path).list_for_card("card_reddit_text")
        if block["block_id"] == "gap_reddit_text"
    )
    assert gap["status"] == "resolved"


def test_reddit_media_post_without_candidates_finalizes_partial(tmp_path: Path, monkeypatch):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_reddit_media",
            "source_id": None,
            "source_platform": "reddit",
            "source_type": "social_post",
            "source_url": "https://www.reddit.com/r/OpenAI/comments/example/media_post/",
            "source_author": "Example",
            "title": "Image post",
            "content": "An image should be attached.",
            "captured_at": "2026-07-19T00:00:00Z",
            "processed_at": "2026-07-19T00:00:00Z",
            "raw_payload": {
                "opencli_record": {
                    "is_self": True,
                    "selftext": "A video post can still carry text.",
                    "secure_media": {
                        "reddit_video": {"fallback_url": "https://v.redd.it/abc/DASH_720.mp4"}
                    },
                }
            },
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.download_card_media",
        lambda *_args, **_kwargs: [],
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            "media_reddit_media",
            TaskType.MEDIA_DOWNLOAD,
            "card",
            "card_reddit_media",
            max_attempts=1,
        )
    )

    first = run_due_tasks(tmp_path, max_workers=1)

    assert first["skipped"] == 1
    media_task = repo.get("media_reddit_media")
    assert media_task is not None
    assert media_task.artifacts_summary["download_status"] == "blocked_no_candidates"
    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize.artifacts_summary["media_gaps"][0]["missing_part"] == "media_download"

    run_due_tasks(tmp_path, max_workers=1)

    card = CardRepository(tmp_path).get_card("card_reddit_media")
    assert card is not None
    assert card["completeness_status"] == "partial"


def test_media_download_task_reports_download_gap_reason(tmp_path: Path, monkeypatch):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_xhs_media_gap",
            "source_id": None,
            "source_platform": "xiaohongshu",
            "source_type": "social_post",
            "source_url": "https://www.xiaohongshu.com/search_result/gap",
            "source_author": "author",
            "title": "小红书笔记",
            "content": "基础正文",
            "captured_at": "2026-07-02T00:00:00Z",
            "processed_at": "2026-07-02T00:00:00Z",
            "raw_payload": {},
        }
    )

    def fake_run(command, **kwargs):
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "AUTH_REQUIRED"})()

    def fake_lease(_site, **kwargs):
        assert kwargs == {"close_tabs": False}
        return nullcontext()

    monkeypatch.setattr("backend.application.enrichment.media.xiaohongshu.subprocess.run", fake_run)
    monkeypatch.setattr(
        "backend.application.enrichment.media.xiaohongshu.opencli_tab_lease",
        fake_lease,
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_download_card_xhs_media_gap",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_xhs_media_gap",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["skipped"] == 1
    task = repo.get("media_download_card_xhs_media_gap")
    assert task.status == TaskStatus.SKIPPED
    assert task.artifacts_summary["download_status"] == "blocked_auth_required"
    assert task.artifacts_summary["gap_reasons"] == ["xiaohongshu_media_download_failed"]
    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": "media_download_card_xhs_media_gap", "status": "skipped"}
    ]


def test_media_download_blocks_when_youtube_expected_media_but_tool_missing(tmp_path: Path, monkeypatch):
    from backend.application.enrichment.media.tooling import MediaToolResult

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_youtube_missing_tool",
            "source_id": None,
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://www.youtube.com/watch?v=abc123",
            "source_author": "OpenAI",
            "title": "Video",
            "content": "Video",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.youtube.run_yt_dlp_download",
        lambda *args, **kwargs: MediaToolResult(status="blocked", files=[], reason="yt_dlp_missing"),
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_download_card_youtube_missing_tool",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_youtube_missing_tool",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["skipped"] == 1
    task = repo.get("media_download_card_youtube_missing_tool")
    assert task is not None
    assert task.status == TaskStatus.SKIPPED
    assert task.artifacts_summary["download_status"] == "blocked_missing_tool"
    assert task.artifacts_summary["media_available"] is False
    gap = next(
        block
        for block in CardContentBlockRepository(tmp_path).list_for_card("card_youtube_missing_tool")
        if block["block_kind"] == "media_download_gap"
    )
    assert gap["raw_payload"]["reason"] == "yt_dlp_missing"
    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": "media_download_card_youtube_missing_tool", "status": "skipped"}
    ]


def test_media_download_timeout_retries_with_persisted_gap(tmp_path: Path, monkeypatch):
    from backend.application.enrichment.media.tooling import MediaToolResult

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_youtube_timeout",
            "source_id": None,
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://www.youtube.com/watch?v=slow",
            "source_author": "OpenAI",
            "title": "Video",
            "content": "Video",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.youtube.run_yt_dlp_download",
        lambda *args, **kwargs: MediaToolResult(status="failed", files=[], reason="timeout"),
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_download_card_youtube_timeout",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_youtube_timeout",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["requeued"] == 1
    task = repo.get("media_download_card_youtube_timeout")
    assert task is not None
    assert task.status == TaskStatus.PENDING
    assert task.next_run_at is not None
    assert task.artifacts_summary["download_status"] == "failed_download_error"
    assert task.artifacts_summary["gap_reasons"] == ["timeout"]
    assert repo.list_tasks(task_type=TaskType.CONTEXT_PACK, limit=10) == []


def test_media_download_success_ignores_stale_gap_reasons(tmp_path: Path, monkeypatch):
    from backend.application.enrichment.media.tooling import DownloadedMediaFile, MediaToolResult

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_youtube_recovered",
            "source_id": None,
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://www.youtube.com/watch?v=recovered",
            "source_author": "OpenAI",
            "title": "Video",
            "content": "Video",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {},
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_card_youtube_recovered_media_download_gap",
            "card_id": "card_youtube_recovered",
            "position": 20,
            "layer": "media",
            "block_kind": "media_download_gap",
            "status": "blocked",
            "body": "command_failed",
            "raw_payload": {"reason": "command_failed"},
        }
    )
    output_file = tmp_path / "data" / "assets" / "card_youtube_recovered" / "youtube" / "recovered.mp4"
    output_file.parent.mkdir(parents=True)
    output_file.write_bytes(b"\x00\x00\x00\x18ftypmp42video")
    monkeypatch.setattr(
        "backend.application.enrichment.media.youtube.run_yt_dlp_download",
        lambda *args, **kwargs: MediaToolResult(
            status="downloaded",
            files=[
                DownloadedMediaFile(
                    path=output_file,
                    relative_path="card_youtube_recovered/youtube/recovered.mp4",
                    kind="video",
                    mime="video/mp4",
                    size=output_file.stat().st_size,
                )
            ],
            metadata={
                "info": {
                    "id": "recovered",
                    "extractor": "youtube",
                    "extractor_key": "Youtube",
                    "webpage_url": "https://www.youtube.com/watch?v=recovered",
                },
                "info_json_sha256": "abc",
            },
        ),
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_download_card_youtube_recovered",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_youtube_recovered",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["succeeded"] == 1
    task = repo.get("media_download_card_youtube_recovered")
    assert task is not None
    assert task.artifacts_summary["download_status"] == "downloaded"
    assert task.artifacts_summary["gap_reasons"] == []
    gap = next(
        block
        for block in CardContentBlockRepository(tmp_path).list_for_card("card_youtube_recovered")
        if block["block_kind"] == "media_download_gap"
    )
    assert gap["status"] == "resolved"


def test_media_download_marks_x_empty_result_as_no_media_found(tmp_path: Path, monkeypatch):
    from backend.application.enrichment.media import x_session

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_x_empty_media",
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/1",
            "source_author": "Example",
            "title": "post",
            "content": "post without downloadable media",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {},
        }
    )

    def fake_run(command, **kwargs):
        return type(
            "Result",
            (),
            {
                "returncode": 66,
                "stdout": "",
                "stderr": "EMPTY_RESULT: twitter download returned no data. No media found in the tweet",
            },
        )()

    monkeypatch.setattr("backend.application.enrichment.media.download.subprocess.run", fake_run)
    monkeypatch.setattr(x_session, "prepare_opencli_browser_session", lambda **_kwargs: {
        "status": "ready",
        "profile": {"alias": "infoos-fetch", "bound": True, "binding_status": "ready"},
    })
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: None)
    monkeypatch.setattr(x_session, "opencli_browser_lock", nullcontext)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_download_card_x_empty_media",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_x_empty_media",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["skipped"] == 1
    task = repo.get("media_download_card_x_empty_media")
    assert task is not None
    assert task.status == TaskStatus.SKIPPED
    assert task.artifacts_summary["download_status"] == "skipped_no_media_found"
    assert task.artifacts_summary["candidate_count"] == 0
    assert task.artifacts_summary["gap_reasons"] == ["x_media_download_no_media_found"]
    gap = next(
        block
        for block in CardContentBlockRepository(tmp_path).list_for_card("card_x_empty_media")
        if block["block_kind"] == "media_download_gap"
    )
    assert gap["status"] == "resolved"
    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize.artifacts_summary["media_gaps"] == []

    run_tasks_by_ids(tmp_path, [finalize.task_id], max_workers=1)

    assert CardRepository(tmp_path).get_card("card_x_empty_media")["completeness_status"] == "complete"


def test_media_download_keeps_x_auth_failure_as_auth_required(tmp_path: Path, monkeypatch):
    from backend.application.enrichment.media import x_session

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_x_auth_media",
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/2",
            "source_author": "Example",
            "title": "post",
            "content": "post requiring auth",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {},
        }
    )

    def fake_run(command, **kwargs):
        return type(
            "Result",
            (),
            {
                "returncode": 66,
                "stdout": "",
                "stderr": "AUTH_REQUIRED: login required to download media",
            },
        )()

    monkeypatch.setattr("backend.application.enrichment.media.download.subprocess.run", fake_run)
    monkeypatch.setattr(x_session, "prepare_opencli_browser_session", lambda **_kwargs: {
        "status": "ready",
        "profile": {"alias": "infoos-fetch", "bound": True, "binding_status": "ready"},
    })
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: None)
    monkeypatch.setattr(x_session, "opencli_browser_lock", nullcontext)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media_download_card_x_auth_media",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_x_auth_media",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["skipped"] == 1
    task = repo.get("media_download_card_x_auth_media")
    assert task is not None
    assert task.status == TaskStatus.SKIPPED
    assert task.artifacts_summary["download_status"] == "blocked_auth_required"
    assert task.artifacts_summary["gap_reasons"] == ["x_media_download_auth_required"]


def test_video_extract_enqueues_transcribe_with_audio_artifact(tmp_path: Path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_1", "source_id": None, "source_platform": "youtube", "source_type": "video",
            "source_url": "https://youtube.example/1", "source_author": "Example", "title": "Video",
            "content": "body", "captured_at": "2026-07-10T00:00:00Z", "processed_at": "2026-07-10T00:00:00Z",
            "raw_payload": {},
        }
    )
    CardAssetRepository(tmp_path).upsert_asset(
        {"asset_id": "video_1", "card_id": "card_1", "asset_kind": "video", "source_url": "https://youtube.example/1", "status": "downloaded"}
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="video_extract_audio_video_1",
            task_type=TaskType.VIDEO_EXTRACT_AUDIO,
            target_kind="asset",
            target_id="video_1",
        )
    )

    run_due_tasks(
        tmp_path,
        handlers={
            TaskType.VIDEO_EXTRACT_AUDIO: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"card_id": "card_1", "audio_path": "/tmp/video_1.wav", "duration_seconds": 13.0},
            }
        },
    )

    transcribe = _one_task(repo, TaskType.VIDEO_TRANSCRIBE)
    assert transcribe is not None
    assert transcribe.artifacts_summary["audio_path"] == "/tmp/video_1.wav"
    assert transcribe.artifacts_summary["duration_seconds"] == 13.0
    assert transcribe.depends_on == (
        DependencySpec(task_id="video_extract_audio_video_1", required_status=TaskStatus.SUCCESS),
    )


def test_video_extract_uses_existing_native_transcript_without_audio(tmp_path: Path, monkeypatch):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_1",
            "source_id": None,
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://youtube.com/watch?v=1",
            "source_author": "Creator",
            "title": "Video",
            "content": "Video body",
            "captured_at": "2026-06-23T00:00:00Z",
            "processed_at": "2026-06-23T00:00:00Z",
        }
    )
    asset_dir = tmp_path / "data" / "assets" / "card_1"
    asset_dir.mkdir(parents=True)
    (asset_dir / "video.mp4").write_bytes(b"fake video")
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video_1",
            "card_id": "card_1",
            "asset_kind": "video",
            "source_url": "https://youtube.com/watch?v=1",
            "local_path": "card_1/video.mp4",
            "status": "downloaded",
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "native_transcript_card_1",
            "card_id": "card_1",
            "position": 1,
            "layer": "original",
            "block_kind": "transcript",
            "status": "preserved",
            "body": "Native transcript from the platform.",
            "segments": [{"start": 0, "end": 1, "text": "Native transcript from the platform."}],
            "source_url": "https://youtube.com/watch?v=1",
            "provenance": "youtube transcript",
            "raw_payload": {"language": "en", "usable_transcript": True},
        }
    )
    called = {"extract_audio": False}

    def fake_extract_audio(path, output_dir):
        called["extract_audio"] = True
        return {"audio_path": None, "duration_seconds": 0, "format": None, "error": "should not run"}

    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription.extract_audio",
        fake_extract_audio,
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="video_extract_audio_video_1",
            task_type=TaskType.VIDEO_EXTRACT_AUDIO,
            target_kind="asset",
            target_id="video_1",
        )
    )

    result = run_due_tasks(tmp_path)

    assert result["skipped"] == 1
    assert called["extract_audio"] is False
    task = repo.get("video_extract_audio_video_1")
    assert task is not None
    assert task.status == TaskStatus.SKIPPED
    assert task.artifacts_summary["reason"] == "skipped_native_transcript_used"
    assert task.artifacts_summary["native_transcript_used"] is True
    assert task.artifacts_summary["transcript_satisfied"] is True
    blocks = CardContentBlockRepository(tmp_path).list_for_card("card_1")
    video_blocks = [block for block in blocks if block["block_kind"] == "video_transcript"]
    assert len(video_blocks) == 1
    assert video_blocks[0]["body"] == "Native transcript from the platform."
    assert video_blocks[0]["raw_payload"]["native_transcript_used"] is True
    asset = CardAssetRepository(tmp_path).get_asset("video_1")
    assert asset is not None
    assert asset["status"] == "understood"
    assert asset["raw_payload"]["transcription_status"] == "skipped_native_transcript_used"
    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize.depends_on == ()
    assert finalize.artifacts_summary["upstream_media_tasks"] == [
        {"task_id": "video_extract_audio_video_1", "status": "skipped"}
    ]
    run_due_tasks(tmp_path, task_types=[TaskType.CARD_FINALIZE], max_workers=1)
    finalized = CardRepository(tmp_path).get_card("card_1")
    assert finalized is not None
    assert finalized["completeness_status"] == "complete"
    assert finalized["missing_reasons"] == []


def test_asr_video_transcript_is_not_reused_as_native_transcript_for_another_asset(tmp_path: Path):
    from backend.application.enrichment.transcripts.native import write_native_transcript_block_if_available

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_1",
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/1",
            "source_author": "Creator",
            "title": "Video",
            "content": "Video body",
            "captured_at": "2026-06-23T00:00:00Z",
            "processed_at": "2026-06-23T00:00:00Z",
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_card_1_video_transcript_video_1",
            "card_id": "card_1",
            "position": 1,
            "layer": "derived",
            "block_kind": "video_transcript",
            "status": "enriched",
            "body": "ASR transcript for the first asset.",
            "segments": [],
            "source_url": "asset:video_1",
            "provenance": "Video transcript via sensevoice-small-int8",
            "raw_payload": {
                "asset_id": "video_1",
                "native_transcript_used": False,
                "route": "sensevoice-small-int8",
            },
        }
    )

    result = write_native_transcript_block_if_available(
        tmp_path,
        {"card_id": "card_1", "asset_id": "video_2"},
    )

    assert result is None
    blocks = CardContentBlockRepository(tmp_path).list_for_card("card_1")
    assert [block["source_url"] for block in blocks] == ["asset:video_1"]


def test_video_transcribe_writes_asr_route_metadata(tmp_path: Path, monkeypatch):
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
    asset_dir = tmp_path / "data" / "assets" / "card_1"
    asset_dir.mkdir(parents=True)
    (asset_dir / "video.mp4").write_bytes(b"fake video")
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video_1",
            "card_id": "card_1",
            "asset_kind": "video",
            "source_url": "https://x.com/elonmusk/status/1",
            "local_path": "card_1/video.mp4",
            "status": "downloaded",
        }
    )

    def fake_extract_video_transcript(*, base_dir, asset, audio_path, duration_seconds=None, quality_mode=None):
        return {
            "status": "success",
            "text": "Local ASR transcript.",
            "segments": [{"start": 0.0, "end": 1.0, "text": "Local ASR transcript."}],
            "language": "zh",
            "language_confidence": 0.93,
            "provider": "sherpa-onnx",
            "model": "sensevoice-small-int8",
            "duration_seconds": 10.0,
            "rtf": 0.35,
            "fallback_chain": ["sensevoice-small-int8"],
            "quality_flags": [],
            "native_transcript_used": False,
            "route": "sensevoice-small-int8",
            "error": None,
        }

    monkeypatch.setattr(
        "backend.application.enrichment.transcripts.service.extract_video_transcript",
        fake_extract_video_transcript,
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="video_transcribe_video_1",
            task_type=TaskType.VIDEO_TRANSCRIBE,
            target_kind="asset",
            target_id="video_1",
            artifacts_summary={"card_id": "card_1", "audio_path": "/tmp/video_1.wav", "duration_seconds": 10.0},
        )
    )

    result = run_due_tasks(tmp_path)

    assert result["succeeded"] == 1
    task = repo.get("video_transcribe_video_1")
    assert task is not None
    assert task.artifacts_summary["provider"] == "sherpa-onnx"
    assert task.artifacts_summary["model"] == "sensevoice-small-int8"
    assert task.artifacts_summary["route"] == "sensevoice-small-int8"
    assert task.artifacts_summary["fallback_chain"] == ["sensevoice-small-int8"]
    blocks = CardContentBlockRepository(tmp_path).list_for_card("card_1")
    transcript = next(block for block in blocks if block["block_kind"] == "video_transcript")
    assert transcript["body"] == "Local ASR transcript."
    assert transcript["segments"] == [{"start": 0.0, "end": 1.0, "text": "Local ASR transcript."}]
    assert transcript["provenance"] == "video_transcribe"
    assert transcript["raw_payload"]["asr_provider"] == "sherpa-onnx"
    assert transcript["raw_payload"]["model"] == "sensevoice-small-int8"
    assert transcript["raw_payload"]["language"] == "zh"
    asset = CardAssetRepository(tmp_path).get_asset("video_1")
    assert asset is not None
    assert asset["status"] == "understood"
    assert asset["raw_payload"]["transcription_status"] == "enriched"


def test_video_transcribe_no_speech_detected_is_not_applicable_without_a_block(
    tmp_path: Path, monkeypatch
):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_no_speech",
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/no-speech",
            "source_author": "Example",
            "title": "Silent video",
            "content": "Video body",
            "captured_at": "2026-06-23T00:00:00Z",
            "processed_at": "2026-06-23T00:00:00Z",
        }
    )
    asset_dir = tmp_path / "data" / "assets" / "card_no_speech"
    asset_dir.mkdir(parents=True)
    (asset_dir / "video.mp4").write_bytes(b"fake video")
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video_no_speech",
            "card_id": "card_no_speech",
            "asset_kind": "video",
            "source_url": "https://x.com/example/status/no-speech",
            "local_path": "card_no_speech/video.mp4",
            "status": "downloaded",
        }
    )
    # A retry that now proves no speech must not leave an older ASR block
    # around to make finalization look transcript-complete.
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "stale_no_speech_transcript",
            "card_id": "card_no_speech",
            "position": 1,
            "layer": "derived",
            "block_kind": "video_transcript",
            "status": "enriched",
            "body": "Stale transcript",
            "segments": [],
            "source_url": "asset:video_no_speech",
            "provenance": "video_transcribe",
            "raw_payload": {"asset_id": "video_no_speech", "native_transcript_used": False},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.transcripts.service.extract_video_transcript",
        lambda **_: {
            "status": "skipped",
            "text": "",
            "segments": [],
            "language": "zh",
            "language_confidence": 0.98,
            "provider": "faster-whisper-cuda",
            "model": "large-v3-turbo",
            "duration_seconds": 10.0,
            "duration_after_vad": 0.0,
            "rtf": 0.1,
            "fallback_chain": ["large-v3-turbo"],
            "quality_flags": [],
            "native_transcript_used": False,
            "route": "large-v3-turbo",
            "error": "no_speech_detected",
        },
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="video_transcribe_no_speech",
            task_type=TaskType.VIDEO_TRANSCRIBE,
            target_kind="asset",
            target_id="video_no_speech",
            artifacts_summary={
                "card_id": "card_no_speech",
                "audio_path": "/tmp/video_no_speech.wav",
                "duration_seconds": 10.0,
            },
        )
    )

    result = run_due_tasks(tmp_path)

    assert result["skipped"] == 1
    task = repo.get("video_transcribe_no_speech")
    assert task is not None
    assert task.status == TaskStatus.SKIPPED
    assert task.artifacts_summary["transcript_not_applicable"] is True
    assert task.artifacts_summary["reason"] == "no_speech_detected"
    blocks = CardContentBlockRepository(tmp_path).list_for_card("card_no_speech")
    assert [block for block in blocks if block["block_kind"] == "video_transcript"] == []
    asset = CardAssetRepository(tmp_path).get_asset("video_no_speech")
    assert asset is not None
    assert asset["raw_payload"]["transcript_not_applicable"] is True
    assert asset["raw_payload"]["transcription_status"] == "not_applicable"
    assert asset["raw_payload"]["transcription_reason_code"] == "no_speech_detected"

    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize.artifacts_summary["media_gaps"] == []
    run_due_tasks(tmp_path, task_types=[TaskType.CARD_FINALIZE], max_workers=1)
    card = CardRepository(tmp_path).get_card("card_no_speech")
    assert card is not None
    assert card["completeness_status"] == "complete"
    assert card["missing_reasons"] == []


def test_video_transcribe_retry_success_clears_stale_blocked_attempt_artifacts(tmp_path: Path, monkeypatch):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_retry",
            "source_id": None,
            "source_platform": "xiaohongshu",
            "source_type": "video",
            "source_url": "https://www.xiaohongshu.com/explore/retry",
            "source_author": "Example",
            "title": "Retry video",
            "content": "Video body",
            "captured_at": "2026-07-14T00:00:00Z",
            "processed_at": "2026-07-14T00:00:00Z",
        }
    )
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video_retry",
            "card_id": "card_retry",
            "asset_kind": "video",
            "source_url": "https://www.xiaohongshu.com/explore/retry",
            "local_path": "card_retry/video.mp4",
            "status": "downloaded",
            "raw_payload": {
                "transcription_status": "blocked_sherpa_onnx_missing",
                "transcription_error": "blocked_sherpa_onnx_missing",
            },
        }
    )

    monkeypatch.setattr(
        "backend.application.enrichment.transcripts.service.extract_video_transcript",
        lambda **_: {
            "status": "success",
            "text": "Successful retry transcript.",
            "segments": [{"start": 0.0, "end": 1.0, "text": "Successful retry transcript."}],
            "language": "zh",
            "provider": "sherpa-onnx",
            "model": "sensevoice-small-int8",
            "quality_flags": [],
            "error": None,
        },
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="video_transcribe_retry",
            task_type=TaskType.VIDEO_TRANSCRIBE,
            target_kind="asset",
            target_id="video_retry",
            status=TaskStatus.BLOCKED,
            attempt=1,
            artifacts_summary={
                "card_id": "card_retry",
                "audio_path": "/tmp/video_retry.wav",
                "reason": "blocked_sherpa_onnx_missing",
                "blocked_reason": "blocked_sherpa_onnx_missing",
                "error": "blocked_sherpa_onnx_missing",
                "quality_flags": ["blocked_sherpa_onnx_missing"],
            },
            error_reason="blocked_sherpa_onnx_missing",
        )
    )
    assert repo.retry("video_transcribe_retry") is True

    result = run_due_tasks(tmp_path)

    assert result["succeeded"] == 1
    task = repo.get("video_transcribe_retry")
    assert task is not None
    assert task.status == TaskStatus.SUCCESS
    assert task.error_reason is None
    for key in ("reason", "blocked_reason", "error", "quality_flags"):
        assert key not in task.artifacts_summary
    transcript = next(
        block
        for block in CardContentBlockRepository(tmp_path).list_for_card("card_retry")
        if block["block_kind"] == "video_transcript"
    )
    assert transcript["status"] == "enriched"
    assert transcript["body"] == "Successful retry transcript."
    asset = CardAssetRepository(tmp_path).get_asset("video_retry")
    assert asset is not None
    assert asset["status"] == "understood"
    assert asset["raw_payload"]["transcription_status"] == "enriched"
    assert "transcription_error" not in asset["raw_payload"]


def test_video_extract_skips_when_ffmpeg_is_missing(tmp_path: Path, monkeypatch):
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
    asset_dir = tmp_path / "data" / "assets" / "card_1"
    asset_dir.mkdir(parents=True)
    (asset_dir / "video.mp4").write_bytes(b"fake")
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video_1",
            "card_id": "card_1",
            "asset_kind": "video",
            "source_url": "https://x.com/elonmusk/status/1",
            "local_path": "card_1/video.mp4",
            "status": "downloaded",
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription.extract_audio",
        lambda path, output_dir: {
            "audio_path": None,
            "duration_seconds": 0,
            "format": None,
            "error": "ffmpeg not installed. Install ffmpeg for video transcription support.",
        },
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="video_extract_audio_video_1",
            task_type=TaskType.VIDEO_EXTRACT_AUDIO,
            target_kind="asset",
            target_id="video_1",
        )
    )

    result = run_due_tasks(tmp_path)

    assert result["skipped"] == 1
    assert repo.get("video_extract_audio_video_1").status == TaskStatus.SKIPPED
    asset = CardAssetRepository(tmp_path).get_asset("video_1")
    assert asset is not None
    assert asset["status"] == "downloaded"
    assert asset["raw_payload"]["understanding_status"] == "failed"
    assert "ffmpeg not installed" in asset["raw_payload"]["transcription_error"]
    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize.depends_on == ()

    second = run_due_tasks(tmp_path)

    assert second["succeeded"] == 1
    assert _one_task(repo, TaskType.CARD_FINALIZE).status == TaskStatus.SUCCESS


def test_video_extract_skips_when_video_has_no_audio_stream(tmp_path: Path, monkeypatch):
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
    asset_dir = tmp_path / "data" / "assets" / "card_1"
    asset_dir.mkdir(parents=True)
    (asset_dir / "video.mp4").write_bytes(b"fake")
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video_1",
            "card_id": "card_1",
            "asset_kind": "video",
            "source_url": "https://x.com/elonmusk/status/1",
            "local_path": "card_1/video.mp4",
            "status": "downloaded",
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription.extract_audio",
        lambda path, output_dir: {
            "audio_path": None,
            "duration_seconds": 0,
            "format": None,
            "error": "Video has no audio stream",
        },
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="video_extract_audio_video_1",
            task_type=TaskType.VIDEO_EXTRACT_AUDIO,
            target_kind="asset",
            target_id="video_1",
        )
    )

    result = run_due_tasks(tmp_path)

    assert result["skipped"] == 1
    assert result["failed"] == 0
    assert repo.get("video_extract_audio_video_1").status == TaskStatus.SKIPPED
    asset = CardAssetRepository(tmp_path).get_asset("video_1")
    assert asset is not None
    assert asset["status"] == "downloaded"
    assert asset["raw_payload"]["transcription_status"] == "not_applicable"
    assert asset["raw_payload"]["transcription_reason_code"] == "no_audio_stream"
    assert asset["raw_payload"]["transcription_error"] == "Video has no audio stream"
    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize.artifacts_summary["media_gaps"] == []
    finalize = _one_task(repo, TaskType.CARD_FINALIZE)
    assert finalize.depends_on == ()
