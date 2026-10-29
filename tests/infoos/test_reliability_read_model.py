from __future__ import annotations

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import (
    CardRepository,
    RunRepository,
    SourceRepository,
    SourceCalibrationRepository,
    TaskEventRepository,
)
from backend.read_models.reliability import percentile, read_reliability_status
from tests.infoos._factories import make_source_payload


NOW = "2026-07-10T12:00:00Z"


def _seed_source(base_dir, *, source_id: str, platform: str) -> None:
    SourceRepository(base_dir).upsert_source(
        make_source_payload(
            source_id=source_id,
            platform=platform,
            connector_id=f"{platform}_{source_id}",
            identifier=f"https://example.com/{source_id}",
            display_name=source_id,
        )
    )


def _seed_run(base_dir, *, run_id: str, source_id: str, status: str, finished_at: str) -> None:
    RunRepository(base_dir).append_run(
        {
            "run_id": run_id,
            "source_id": source_id,
            "scheduled_for": finished_at,
            "started_at": finished_at,
            "finished_at": finished_at,
            "status": status,
            "task_type": "fetch",
        }
    )


def _seed_task(base_dir, *, task_id: str, task_type: TaskType, status: TaskStatus, created_at: str, lease_expires_at: str | None = None) -> None:
    TaskRepository.for_base_dir(base_dir).enqueue(
        Task(
            task_id=task_id,
            task_type=task_type,
            target_kind="card",
            target_id=f"target-{task_id}",
            status=status,
            lease_expires_at=lease_expires_at,
        )
    )
    with TaskRepository.for_base_dir(base_dir)._connect() as conn:
        conn.execute(
            "update tasks set created_at = ?, started_at = ? where task_id = ?",
            (created_at, created_at if status == TaskStatus.RUNNING else None, task_id),
        )


def test_reliability_summary_groups_success_by_platform_and_failure_class(tmp_path):
    _seed_source(tmp_path, source_id="rss", platform="rss")
    _seed_source(tmp_path, source_id="x", platform="x")
    _seed_run(tmp_path, run_id="rss-success", source_id="rss", status="success", finished_at="2026-07-10T11:00:00Z")
    _seed_run(tmp_path, run_id="rss-failure", source_id="rss", status="failed", finished_at="2026-07-10T10:00:00Z")
    _seed_run(tmp_path, run_id="x-success", source_id="x", status="success", finished_at="2026-07-10T11:30:00Z")
    TaskRepository.for_base_dir(tmp_path).enqueue(
        Task(
            task_id="fetch-rss",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="rss",
        )
    )
    TaskEventRepository(tmp_path).append(
        event_type="failed",
        task_id="fetch-rss",
        failure_class="auth_required",
        occurred_at="2026-07-10T10:00:00Z",
    )

    result = read_reliability_status(tmp_path, now=NOW)

    assert result["acquisition"]["rss"] == {
        "runs_24h": 2,
        "success_rate_24h": 0.5,
        "runs_7d": 2,
        "success_rate_7d": 0.5,
        "failure_classes": {"auth_required": 1},
    }
    assert result["acquisition"]["x"]["success_rate_24h"] == 1.0


def test_reliability_summary_counts_calibration_results_and_queue(tmp_path):
    _seed_source(tmp_path, source_id="rss", platform="rss")
    SourceCalibrationRepository(tmp_path).append(
        calibration_id="cal_rss_success",
        source_id="rss",
        platform="rss",
        source_type="feed",
        identifier="https://example.com/rss",
        result="success",
        task_id="source_calibrate_rss_success",
        visible_count=1,
        sample_count=1,
        calibrated_at="2026-07-10T11:00:00Z",
    )
    _seed_task(
        tmp_path,
        task_id="source_calibrate_pending",
        task_type=TaskType.SOURCE_CALIBRATE,
        status=TaskStatus.PENDING,
        created_at="2026-07-10T11:30:00Z",
    )

    result = read_reliability_status(tmp_path, now=NOW)

    assert result["calibration"]["evidence"] == {"total": 1, "success": 1, "failed": 0, "success_rate": 1.0}
    assert result["calibration"]["queue"] == {
        "pending": 1,
        "running": 0,
        "blocked": 0,
        "failed": 0,
        "success": 0,
        "auth_required": 0,
        "oldest_pending_seconds": 1800.0,
    }
    assert result["calibration"]["by_platform"]["rss"]["success"] == 1


def test_reliability_does_not_report_recovered_auth_task_as_auth_required(tmp_path):
    TaskRepository.for_base_dir(tmp_path).enqueue(
        Task(
            task_id="source_calibrate_recovered",
            task_type=TaskType.SOURCE_CALIBRATE,
            target_kind="source",
            target_id="rss_recovered",
            status=TaskStatus.SUCCESS,
            last_failure_class="platform_not_logged_in",
        )
    )

    result = read_reliability_status(tmp_path, now=NOW)

    assert result["calibration"]["queue"]["success"] == 1
    assert result["calibration"]["queue"]["auth_required"] == 0


def test_reliability_summary_reports_queue_lease_age_and_pipeline_percentiles(tmp_path):
    _seed_task(tmp_path, task_id="pending", task_type=TaskType.FETCH, status=TaskStatus.PENDING, created_at="2026-07-10T11:58:00Z")
    _seed_task(tmp_path, task_id="running", task_type=TaskType.CARD_BUILD, status=TaskStatus.RUNNING, created_at="2026-07-10T11:57:00Z", lease_expires_at="2026-07-10T11:59:00Z")
    _seed_task(tmp_path, task_id="card-1", task_type=TaskType.CARD_BUILD, status=TaskStatus.SUCCESS, created_at="2026-07-10T11:00:00Z")
    events = TaskEventRepository(tmp_path)
    events.append(event_type="acquisition_committed", task_id="fetch-1", chain_id="chain-1", occurred_at="2026-07-10T10:00:00Z")
    events.append(event_type="completed", task_id="card-1", chain_id="chain-1", occurred_at="2026-07-10T10:00:10Z")

    result = read_reliability_status(tmp_path, now=NOW)

    assert result["queue"] == {
        "pending": 1,
        "running": 1,
        "blocked": 0,
        "failed": 0,
        "oldest_pending_seconds": 120.0,
        "oldest_running_seconds": 180.0,
    }
    assert result["leases"]["expired"] == 1
    assert result["pipeline"]["fetch_to_card_p50_seconds"] == 10.0
    assert result["pipeline"]["fetch_to_card_p95_seconds"] == 10.0
    assert "fetch_to_output_p50_seconds" not in result["pipeline"]
    assert result["pipeline"]["stage_counts"]["card_build"] == 2
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == 4.0


def test_reliability_summary_counts_current_card_chain(tmp_path):
    _seed_source(tmp_path, source_id="rss", platform="rss")
    _seed_run(tmp_path, run_id="run-1", source_id="rss", status="success", finished_at=NOW)
    CardRepository(tmp_path).upsert_card({
        "card_id": "card-1", "source_platform": "rss", "source_type": "feed",
        "source_url": "https://example.com/card-1", "title": "Card", "content": "body",
        "captured_at": NOW, "processed_at": NOW, "source_run_id": "run-1",
        "raw_connector_id": "rss_rss", "raw_item_id": "raw-1",
    })
    from backend.infrastructure.storage.database import connect
    from backend.infrastructure.storage.paths import resolve_sqlite_path
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("""insert into raw_items
            (connector_id, raw_item_id, source_id, run_id, source_platform, source_type,
             source_url, captured_at, first_seen_at, last_seen_at)
            values (?,?,?,?,?,?,?,?,?,?)""",
            ("rss_rss", "raw-1", "rss", "run-1", "rss", "feed",
             "https://example.com/card-1", NOW, NOW, NOW))

    result = read_reliability_status(tmp_path, now=NOW)

    assert result["intelligence"] == {
        "successful_source_runs": 1, "raw_items_from_successful_runs": 1,
        "cards_from_successful_runs": 1, "raw_to_card_yield": 1.0,
        "cards": 1,
    }
    assert result["product_yield"] == {
        "successful_fetch_raw_items": 1,
        "cards_from_successful_fetch": 1,
        "fetch_to_card_yield": 1.0,
    }
