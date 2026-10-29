from __future__ import annotations

from pathlib import Path

from backend.application.enrichment.media.download import download_card_media
from backend.application.enrichment.media.tooling import DownloadedMediaFile, MediaToolResult
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks


def _youtube_card(tmp_path: Path, card_id: str = "card_youtube_media") -> None:
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": card_id,
            "source_id": None,
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://www.youtube.com/watch?v=abc123",
            "source_author": "OpenAI",
            "title": "YouTube video",
            "content": "YouTube video",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {},
        }
    )


def _metadata(video_id: str = "abc123") -> dict:
    return {
        "info": {
            "id": video_id,
            "extractor": "youtube",
            "extractor_key": "Youtube",
            "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
            "uploader": "OpenAI",
            "timestamp": 1,
        },
        "info_json_sha256": "abc",
        "version": "test",
    }


def test_youtube_media_download_uses_yt_dlp_and_creates_video_asset(monkeypatch, tmp_path):
    _youtube_card(tmp_path)
    profile_dir = tmp_path / "chrome-profile"
    monkeypatch.setattr(
        "backend.application.enrichment.media.youtube.chrome_profile_dir",
        lambda: profile_dir,
    )

    def fake_run(url, output_dir, *, cookies_from_browser=None):
        assert cookies_from_browser == f"chrome:{profile_dir}"
        video_path = output_dir / "abc123.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42video")
        thumb_path = output_dir / "abc123.jpg"
        thumb_path.write_bytes(b"\xff\xd8\xff\xe0image")
        return MediaToolResult(
            status="downloaded",
            reason=None,
            files=[
                DownloadedMediaFile(
                    video_path,
                    "card_youtube_media/youtube/abc123.mp4",
                    "video",
                    "video/mp4",
                    video_path.stat().st_size,
                ),
                DownloadedMediaFile(
                    thumb_path,
                    "card_youtube_media/youtube/abc123.jpg",
                    "image",
                    "image/jpeg",
                    thumb_path.stat().st_size,
                ),
            ],
            metadata=_metadata(),
        )

    monkeypatch.setattr("backend.application.enrichment.media.youtube.run_yt_dlp_download", fake_run)

    blocks = download_card_media("card_youtube_media", tmp_path)

    assert {block["block_kind"] for block in blocks} == {"image", "video"}
    assets = CardAssetRepository(tmp_path).list_for_card("card_youtube_media")
    assert {asset["asset_kind"] for asset in assets} == {"image", "video"}
    assert all(asset["raw_payload"]["strategy"] == "yt_dlp" for asset in assets)


def test_youtube_missing_yt_dlp_records_gap(monkeypatch, tmp_path):
    _youtube_card(tmp_path, "card_youtube_gap")
    monkeypatch.setattr(
        "backend.application.enrichment.media.youtube.run_yt_dlp_download",
        lambda *args, **kwargs: MediaToolResult(status="blocked", files=[], reason="yt_dlp_missing"),
    )

    blocks = download_card_media("card_youtube_gap", tmp_path)

    assert blocks == []
    gap = next(
        block
        for block in CardContentBlockRepository(tmp_path).list_for_card("card_youtube_gap")
        if block["block_kind"] == "media_download_gap"
    )
    assert gap["status"] == "blocked"
    assert gap["raw_payload"]["reason"] == "yt_dlp_missing"
    assert gap["raw_payload"]["details"]["strategy"] == "yt_dlp"


def test_youtube_thumbnail_only_is_terminal_gap_without_orphan_asset(monkeypatch, tmp_path):
    _youtube_card(tmp_path, "card_youtube_relative")

    def fake_run(url, output_dir, *, cookies_from_browser=None):
        thumb_path = output_dir / "abc123.jpg"
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(b"\xff\xd8\xff\xe0image")
        return MediaToolResult(
            status="downloaded",
            reason=None,
            files=[
                DownloadedMediaFile(
                    thumb_path,
                    "youtube/abc123.jpg",
                    "image",
                    "image/jpeg",
                    thumb_path.stat().st_size,
                ),
            ],
            metadata=_metadata(),
        )

    monkeypatch.setattr("backend.application.enrichment.media.youtube.run_yt_dlp_download", fake_run)

    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="media-youtube-thumbnail-only",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card_youtube_relative",
    ))
    result = run_due_tasks(tmp_path, task_types=[TaskType.MEDIA_DOWNLOAD])

    assert result["skipped"] == 1
    task = repo.get("media-youtube-thumbnail-only")
    assert task is not None and task.status == TaskStatus.SKIPPED
    assert task.artifacts_summary["gap_reasons"] == ["youtube_media_download_no_video_file"]
    assert CardAssetRepository(tmp_path).list_for_card("card_youtube_relative") == []
    blocks = CardContentBlockRepository(tmp_path).list_for_card("card_youtube_relative")
    assert not any(block["block_kind"] == "image" for block in blocks)
    assert len(repo.list_by_target(
        target_kind="card", target_id="card_youtube_relative", task_type=TaskType.CARD_FINALIZE
    )) == 1


def test_usable_subtitle_skips_media_as_success_and_enqueues_finalize(monkeypatch, tmp_path):
    _youtube_card(tmp_path, "card_youtube_subtitle")
    block_repo = CardContentBlockRepository(tmp_path)
    block_repo.upsert_block(
        {
            "block_id": "block_card_youtube_subtitle_transcript",
            "card_id": "card_youtube_subtitle",
            "position": 0,
            "layer": "context",
            "block_kind": "transcript",
            "status": "enriched",
            "body": "real platform subtitle",
            "segments": [{"start": 0, "end": 1, "text": "real platform subtitle"}],
            "source_url": "https://www.youtube.com/watch?v=abc123",
            "provenance": "YouTube platform subtitle",
            "raw_payload": {"usable_transcript": True, "video_id": "abc123"},
        }
    )
    block_repo.upsert_block(
        {
            "block_id": "block_card_youtube_subtitle_youtube_subtitle_attempt",
            "card_id": "card_youtube_subtitle",
            "position": 1,
            "layer": "context",
            "block_kind": "enrichment",
            "status": "done",
            "body": "",
            "source_url": "https://www.youtube.com/watch?v=abc123",
            "provenance": "YouTube subtitle attempt",
            "raw_payload": {"subtitle_attempt_status": "usable", "usable_transcript": True},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.download_youtube_card_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("video downloader must not run")),
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="media-youtube-subtitle",
            task_type=TaskType.MEDIA_DOWNLOAD,
            target_kind="card",
            target_id="card_youtube_subtitle",
        )
    )

    result = run_due_tasks(tmp_path, task_types=[TaskType.MEDIA_DOWNLOAD])
    media = repo.get("media-youtube-subtitle")
    assert result["succeeded"] == 1
    assert media is not None and media.status == TaskStatus.SUCCESS
    assert media.artifacts_summary["download_status"] == "skipped_transcript_available"
    assert CardAssetRepository(tmp_path).list_for_card("card_youtube_subtitle") == []
    assert not any(block["block_kind"] == "media_download_gap" for block in block_repo.list_for_card("card_youtube_subtitle"))
    assert repo.list_by_target(
        target_kind="card", target_id="card_youtube_subtitle", task_type=TaskType.VIDEO_EXTRACT_AUDIO
    ) == []
    assert repo.list_by_target(
        target_kind="card", target_id="card_youtube_subtitle", task_type=TaskType.VIDEO_TRANSCRIBE
    ) == []
    assert len(repo.list_by_target(
        target_kind="card", target_id="card_youtube_subtitle", task_type=TaskType.CARD_FINALIZE
    )) == 1


def test_thumbnail_without_usable_subtitle_still_calls_youtube_downloader(monkeypatch, tmp_path):
    _youtube_card(tmp_path, "card_youtube_thumbnail")
    card = CardRepository(tmp_path).get_card("card_youtube_thumbnail")
    assert card is not None
    card["raw_payload"] = {"thumbnail_url": "https://i.ytimg.com/vi/abc123/default.jpg"}
    CardRepository(tmp_path).upsert_card(card)
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.download_youtube_card_media",
        lambda card_id, *_args, **_kwargs: calls.append(card_id) or [],
    )
    download_card_media("card_youtube_thumbnail", tmp_path)
    assert calls == ["card_youtube_thumbnail"]


def test_youtube_video_info_mismatch_writes_zero_assets_and_cleans_staging(monkeypatch, tmp_path):
    _youtube_card(tmp_path, "card_youtube_mismatch")

    def fake_run(url, output_dir, **_kwargs):
        media = output_dir / "wrong.mp4"
        media.write_bytes(b"\x00\x00\x00\x18ftypmp42video")
        return MediaToolResult(
            status="downloaded",
            files=[DownloadedMediaFile(media, "wrong.mp4", "video", "video/mp4", media.stat().st_size)],
            metadata=_metadata("wrong"),
        )

    monkeypatch.setattr("backend.application.enrichment.media.youtube.run_yt_dlp_download", fake_run)
    assert download_card_media("card_youtube_mismatch", tmp_path) == []
    assert CardAssetRepository(tmp_path).list_for_card("card_youtube_mismatch") == []
    assert not list((tmp_path / "data" / "assets" / "card_youtube_mismatch" / "youtube").glob(".video-*"))


def test_rejected_video_with_valid_thumbnail_is_terminal_gap(monkeypatch, tmp_path):
    _youtube_card(tmp_path, "card_youtube_rejected_video")

    def fake_run(url, output_dir, **_kwargs):
        video = output_dir / "abc123.mp4"
        thumb = output_dir / "abc123.jpg"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42video")
        thumb.write_bytes(b"\xff\xd8\xff\xe0image")
        return MediaToolResult(
            status="downloaded",
            files=[
                DownloadedMediaFile(video, "abc123.mp4", "video", "video/mp4", 201 * 1024 * 1024),
                DownloadedMediaFile(thumb, "abc123.jpg", "image", "image/jpeg", thumb.stat().st_size),
            ],
            metadata=_metadata(),
        )

    monkeypatch.setattr("backend.application.enrichment.media.youtube.run_yt_dlp_download", fake_run)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="media-youtube-rejected-video",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card_youtube_rejected_video",
    ))
    result = run_due_tasks(tmp_path, task_types=[TaskType.MEDIA_DOWNLOAD])
    task = repo.get("media-youtube-rejected-video")
    assert result["skipped"] == 1
    assert task is not None and task.artifacts_summary["gap_reasons"] == ["youtube_media_download_video_rejected"]
    assert CardAssetRepository(tmp_path).list_for_card("card_youtube_rejected_video") == []
    assert repo.list_tasks(task_type=TaskType.VIDEO_EXTRACT_AUDIO) == []
