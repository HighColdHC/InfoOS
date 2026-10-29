from __future__ import annotations

from pathlib import Path

from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.lanes import ExecutionLane, execution_lane_for
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.infrastructure.storage.repositories import CardRepository, SourceRepository


def _upsert_source(base_dir: Path, *, source_id: str, platform: str, source_type: str, identifier: str) -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": source_id,
            "platform": platform,
            "source_type": source_type,
            "identifier": identifier,
            "display_name": source_id,
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": source_id,
        }
    )


def test_rss_fetch_uses_public_lane(tmp_path: Path):
    _upsert_source(
        tmp_path,
        source_id="rss_feed",
        platform="rss",
        source_type="feed",
        identifier="https://example.com/feed.xml",
    )

    lane = execution_lane_for(
        Task("fetch_rss", TaskType.FETCH, "source", "rss_feed"),
        tmp_path,
    )

    assert lane is ExecutionLane.PUBLIC_ACQUISITION


def test_opencli_fetch_uses_browser_lane(tmp_path: Path):
    _upsert_source(
        tmp_path,
        source_id="x_creator",
        platform="x",
        source_type="creator",
        identifier="openai",
    )

    lane = execution_lane_for(
        Task("fetch_x", TaskType.FETCH, "source", "x_creator"),
        tmp_path,
    )

    assert lane is ExecutionLane.BROWSER_ACQUISITION


def _upsert_card(base_dir: Path, *, card_id: str, platform: str) -> None:
    CardRepository(base_dir).upsert_card(
        {
            "card_id": card_id,
            "source_platform": platform,
            "source_type": "social_post",
            "source_url": f"https://example.com/{card_id}",
            "title": card_id,
            "content": "content https://example.feishu.cn/docx/SCYS" if platform == "scys" else "content",
            "captured_at": "2026-07-15T00:00:00Z",
            "processed_at": "2026-07-15T00:00:00Z",
            "raw_payload": {},
        }
    )


def test_browser_content_enrich_platforms_use_browser_lane(tmp_path: Path):
    for platform in ("x", "xiaohongshu", "scys"):
        card_id = f"{platform}_card"
        _upsert_card(tmp_path, card_id=card_id, platform=platform)
        task = Task(f"enrich_{platform}", TaskType.CONTENT_ENRICH, "card", card_id)
        assert execution_lane_for(task, tmp_path) is ExecutionLane.BROWSER_ACQUISITION


def test_browser_media_platforms_use_browser_lane(tmp_path: Path):
    for platform in ("x", "xiaohongshu", "douyin"):
        card_id = f"{platform}_card"
        _upsert_card(tmp_path, card_id=card_id, platform=platform)
        task = Task(f"media_{platform}", TaskType.MEDIA_DOWNLOAD, "card", card_id)
        assert execution_lane_for(task, tmp_path) is ExecutionLane.BROWSER_ACQUISITION


def test_youtube_media_download_stays_default_lane(tmp_path: Path):
    _upsert_card(tmp_path, card_id="youtube_card", platform="youtube")
    task = Task("media_youtube", TaskType.MEDIA_DOWNLOAD, "card", "youtube_card")
    assert execution_lane_for(task, tmp_path) is ExecutionLane.DEFAULT


def test_data_export_uses_local_io_lane(tmp_path: Path):
    task = Task("export_1", TaskType.DATA_EXPORT, "export", "export_1")
    assert execution_lane_for(task, tmp_path) is ExecutionLane.LOCAL_IO


def test_data_export_lane_is_serial_even_when_default_workers_are_higher(tmp_path: Path):
    repository = TaskRepository.for_base_dir(tmp_path)
    for index in range(2):
        repository.enqueue(Task(f"export_{index}", TaskType.DATA_EXPORT, "export", f"export_{index}"))

    result = run_due_tasks(
        tmp_path,
        now="2026-07-16T00:00:00Z",
        max_workers=8,
        handlers={TaskType.DATA_EXPORT: lambda _task, _base_dir: {"status": "success", "artifacts": {}}},
    )

    assert result["succeeded"] == 2
    assert result["lane_worker_counts"] == {"local_io": 1}
