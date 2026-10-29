from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.audio_retention import cleanup_retained_audio
from tests.infoos.storage.migration_test_helpers import migrate_database_path
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.task_events import TaskEventRepository


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
OLD = "2026-07-20T12:00:00Z"


def _setup(tmp_path: Path) -> TaskRepository:
    migrate_database_path(resolve_sqlite_path(tmp_path))
    return TaskRepository.for_base_dir(tmp_path)


def _audio_file(tmp_path: Path, name: str, *, age_days: int = 9) -> Path:
    path = tmp_path / "data" / "audio" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"wav-data")
    modified = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (modified, modified))
    return path


def _transcription_task(
    task_id: str,
    audio_path: Path,
    *,
    status: TaskStatus = TaskStatus.SUCCESS,
    finished_at: str | None = OLD,
) -> Task:
    return Task(
        task_id=task_id,
        task_type=TaskType.VIDEO_TRANSCRIBE,
        target_kind="asset",
        target_id=f"asset_{task_id}",
        status=status,
        finished_at=finished_at,
        artifacts_summary={
            "audio_path": str(audio_path),
            "chain_id": f"chain_{task_id}",
        },
    )


def test_cleanup_deletes_only_expired_successful_wav_and_records_disposition(
    tmp_path: Path,
):
    repo = _setup(tmp_path)
    audio_path = _audio_file(tmp_path, "expired.wav")
    repo.enqueue(_transcription_task("transcribe_success", audio_path))

    result = cleanup_retained_audio(
        tmp_path,
        now=NOW,
        retention_days=7,
        limit=50,
    )

    assert result == {
        "enabled": True,
        "retention_days": 7,
        "scanned": 1,
        "deleted": 1,
        "missing": 0,
        "skipped": 0,
        "bytes_reclaimed": 8,
        "errors": [],
    }
    assert not audio_path.exists()
    artifacts = repo.get("transcribe_success").artifacts_summary
    assert artifacts["audio_retention_status"] == "deleted"
    assert artifacts["audio_retention_bytes_reclaimed"] == 8
    assert artifacts["audio_retention_processed_at"] == "2026-07-29T12:00:00Z"
    events = TaskEventRepository(tmp_path).list_events(
        task_id="transcribe_success",
        event_type="audio_retention_deleted",
    )
    assert len(events) == 1
    assert events[0]["payload"] == {
        "audio_file": "expired.wav",
        "bytes_reclaimed": 8,
        "retention_days": 7,
    }


def test_cleanup_preserves_wav_referenced_by_non_success_task(tmp_path: Path):
    repo = _setup(tmp_path)
    audio_path = _audio_file(tmp_path, "retryable.wav")
    repo.enqueue(_transcription_task("transcribe_success", audio_path))
    repo.enqueue(
        _transcription_task(
            "transcribe_failed",
            audio_path,
            status=TaskStatus.FAILED,
            finished_at=None,
        )
    )

    result = cleanup_retained_audio(
        tmp_path,
        now=NOW,
        retention_days=7,
        limit=50,
    )

    assert result["scanned"] == 0
    assert result["deleted"] == 0
    assert audio_path.exists()


def test_cleanup_preserves_recent_file_even_when_task_is_old(tmp_path: Path):
    repo = _setup(tmp_path)
    audio_path = _audio_file(tmp_path, "recent.wav", age_days=1)
    repo.enqueue(_transcription_task("transcribe_success", audio_path))

    result = cleanup_retained_audio(
        tmp_path,
        now=NOW,
        retention_days=7,
        limit=50,
    )

    assert result["scanned"] == 1
    assert result["skipped"] == 1
    assert result["errors"] == []
    assert audio_path.exists()
    assert "audio_retention_status" not in repo.get(
        "transcribe_success"
    ).artifacts_summary


def test_cleanup_rejects_path_outside_audio_root(tmp_path: Path):
    repo = _setup(tmp_path)
    outside_path = tmp_path / "outside.wav"
    outside_path.write_bytes(b"do-not-delete")
    modified = (NOW - timedelta(days=9)).timestamp()
    os.utime(outside_path, (modified, modified))
    repo.enqueue(_transcription_task("transcribe_success", outside_path))

    result = cleanup_retained_audio(
        tmp_path,
        now=NOW,
        retention_days=7,
        limit=50,
    )

    assert result["scanned"] == 1
    assert result["deleted"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == [
        {
            "audio_file": "outside.wav",
            "reason": "outside_audio_root",
        }
    ]
    assert outside_path.exists()


def test_cleanup_can_be_disabled_with_zero_retention_days(tmp_path: Path):
    _setup(tmp_path)

    result = cleanup_retained_audio(
        tmp_path,
        now=NOW,
        retention_days=0,
        limit=50,
    )

    assert result == {
        "enabled": False,
        "retention_days": 0,
        "scanned": 0,
        "deleted": 0,
        "missing": 0,
        "skipped": 0,
        "bytes_reclaimed": 0,
        "errors": [],
    }
