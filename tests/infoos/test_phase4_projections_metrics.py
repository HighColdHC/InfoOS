from __future__ import annotations

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.sources.calibration import source_calibration_version
from backend.infrastructure.storage.repositories import SourceRepository
from backend.read_models.reliability import read_reliability_status
from backend.read_models.source_management import read_source_detail
from backend.read_models.task_manager import read_task_list
from tests.infoos._factories import make_source_payload


def _source(base_dir, source_id: str, *, status: str = "implemented_uncalibrated", enabled: bool = True):
    payload = make_source_payload(
            source_id=source_id,
            connector_id=source_id,
            identifier=f"https://example.com/{source_id}",
            connector_status=status,
            enabled=enabled,
        )
    payload["connector_status"] = status
    SourceRepository(base_dir).upsert_source(payload)
    return SourceRepository(base_dir).get_source(source_id)


def _initial(source, *, status: TaskStatus, attempt: int = 1, artifacts=None):
    return Task(
        task_id=f"fetch_{source['source_id']}_{status.value}",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id=source["source_id"],
        status=status,
        attempt=attempt,
        input_version=source_calibration_version(source),
        last_failure_class="platform_not_logged_in" if status == TaskStatus.BLOCKED else None,
        artifacts_summary={"initial_fetch": True, "source_input_version": source_calibration_version(source), **(artifacts or {})},
    )


def test_source_health_derives_phase4_readiness_from_current_initial_fetch_only(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    awaiting = _source(tmp_path, "awaiting")
    running = _source(tmp_path, "running")
    retrying = _source(tmp_path, "retrying")
    action = _source(tmp_path, "action")
    ready = _source(tmp_path, "ready", status="supported")
    disabled = _source(tmp_path, "disabled", enabled=False)
    repo.enqueue(_initial(running, status=TaskStatus.RUNNING))
    repo.enqueue(_initial(retrying, status=TaskStatus.PENDING, attempt=2))
    repo.enqueue(_initial(action, status=TaskStatus.BLOCKED))
    repo.enqueue(_initial(ready, status=TaskStatus.SUCCESS, artifacts={"no_data": True}))
    # Stale history must not override the current source version.
    repo.enqueue(Task(task_id="fetch_awaiting_stale", task_type=TaskType.FETCH, target_kind="source", target_id="awaiting", status=TaskStatus.SUCCESS, input_version="stale", artifacts_summary={"initial_fetch": True}))

    rows = {source["source_id"]: read_source_detail(tmp_path, source["source_id"])["source"]
            for source in SourceRepository(tmp_path).list_sources()}

    assert rows[awaiting["source_id"]]["readiness_status"] == "awaiting_initial_fetch"
    assert rows[running["source_id"]]["readiness_status"] == "initial_fetch_running"
    assert rows[retrying["source_id"]]["readiness_status"] == "initial_fetch_retrying"
    assert rows[action["source_id"]]["readiness_status"] == "needs_action"
    assert rows[ready["source_id"]]["readiness_status"] == "ready"
    assert rows[disabled["source_id"]]["readiness_status"] == "disabled"


def test_task_list_projects_initial_fetch_once_and_preserves_media_gaps(tmp_path):
    source = _source(tmp_path, "chain-source", status="supported")
    repo = TaskRepository.for_base_dir(tmp_path)
    common = {"chain_id": "chain-phase4", "chain_root_task_id": "fetch-chain"}
    repo.enqueue(Task(
        task_id="fetch-chain", task_type=TaskType.FETCH, target_kind="source", target_id=source["source_id"],
        status=TaskStatus.SUCCESS, input_version=source_calibration_version(source), artifacts_summary={**common, "initial_fetch": True},
    ))
    repo.enqueue(Task(
        task_id="media-chain", task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card", target_id="card-chain",
        status=TaskStatus.SKIPPED, artifacts_summary={**common, "gap_reasons": ["x_media_download_browser_session"]},
    ))
    repo.enqueue(Task(
        task_id="context-chain", task_type=TaskType.CONTEXT_PACK, target_kind="card", target_id="card-chain",
        status=TaskStatus.SUCCESS, artifacts_summary={**common, "media_gaps": [{"missing_part": "media_download", "reason": "x_media_download_browser_session", "status": "skipped"}]},
    ))
    repo.enqueue(Task(
        task_id="output-chain", task_type=TaskType.OUTPUT_GENERATE, target_kind="card", target_id="card-chain",
        status=TaskStatus.SUCCESS, artifacts_summary=common,
    ))

    rows = read_task_list(tmp_path, chain_id="chain-phase4")
    projected = {row["task_id"]: row for row in rows}

    assert [row["display_stage"] for row in rows].count("initial_fetch") == 1
    assert "source_calibrate_history" not in [row["display_stage"] for row in rows]
    assert projected["media-chain"]["status"] == "skipped"
    assert projected["media-chain"]["artifacts_summary"]["gap_reasons"] == ["x_media_download_browser_session"]
    assert projected["context-chain"]["artifacts_summary"]["media_gaps"][0]["missing_part"] == "media_download"


def test_task_list_preserves_no_media_expected_skip(tmp_path):
    source = _source(tmp_path, "plain-source", status="supported")
    repo = TaskRepository.for_base_dir(tmp_path)
    common = {"chain_id": "chain-plain", "chain_root_task_id": "fetch-plain"}
    repo.enqueue(Task(
        task_id="fetch-plain", task_type=TaskType.FETCH, target_kind="source", target_id=source["source_id"],
        status=TaskStatus.SUCCESS, input_version=source_calibration_version(source), artifacts_summary=common,
    ))
    repo.enqueue(Task(
        task_id="media-plain", task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card", target_id="card-plain",
        status=TaskStatus.SKIPPED,
        artifacts_summary={**common, "download_status": "skipped_no_media_expected", "gap_reasons": []},
    ))
    repo.enqueue(Task(
        task_id="context-plain", task_type=TaskType.CONTEXT_PACK, target_kind="card", target_id="card-plain",
        status=TaskStatus.SUCCESS, artifacts_summary={**common, "media_gaps": []},
    ))
    repo.enqueue(Task(
        task_id="output-plain", task_type=TaskType.OUTPUT_GENERATE, target_kind="card", target_id="card-plain",
        status=TaskStatus.SUCCESS, artifacts_summary=common,
    ))

    rows = read_task_list(tmp_path, chain_id="chain-plain")
    projected = {row["task_id"]: row for row in rows}

    assert all(row["retryable"] is False for row in rows)
    assert projected["media-plain"]["status"] == "skipped"
    assert projected["media-plain"]["artifacts_summary"]["download_status"] == "skipped_no_media_expected"
    assert projected["media-plain"]["artifacts_summary"]["gap_reasons"] == []
    assert projected["context-plain"]["artifacts_summary"]["media_gaps"] == []


def test_task_list_preserves_active_task_status(tmp_path):
    source = _source(tmp_path, "active-source", status="supported")
    repo = TaskRepository.for_base_dir(tmp_path)
    for status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        suffix = status.value
        common = {"chain_id": f"chain-{suffix}", "chain_root_task_id": f"fetch-{suffix}"}
        repo.enqueue(Task(
            task_id=f"fetch-{suffix}", task_type=TaskType.FETCH, target_kind="source", target_id=source["source_id"],
            status=TaskStatus.SUCCESS, input_version=source_calibration_version(source), artifacts_summary=common,
        ))
        repo.enqueue(Task(
            task_id=f"media-{suffix}", task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card", target_id=f"card-{suffix}",
            status=TaskStatus.SKIPPED,
            artifacts_summary={**common, "gap_reasons": ["x_media_download_browser_session"]},
        ))
        repo.enqueue(Task(
            task_id=f"output-{suffix}", task_type=TaskType.OUTPUT_GENERATE, target_kind="card", target_id=f"card-{suffix}",
            status=TaskStatus.SUCCESS, artifacts_summary=common,
        ))
        repo.enqueue(Task(
            task_id=f"topic-{suffix}", task_type=TaskType.TOPIC_UPDATE, target_kind="card", target_id=f"card-{suffix}",
            status=status, artifacts_summary=common,
        ))

    rows = {row["task_id"]: row for row in read_task_list(tmp_path)}

    assert rows["topic-pending"]["status"] == "pending"
    assert rows["topic-running"]["status"] == "running"
    assert rows["topic-pending"]["chain_id"] == "chain-pending"
    assert rows["topic-running"]["chain_id"] == "chain-running"


def test_reliability_metrics_use_current_source_versions_without_retry_inflation(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    success = _source(tmp_path, "metric-success", status="supported")
    blocked = _source(tmp_path, "metric-blocked")
    pending = _source(tmp_path, "metric-pending")
    repo.enqueue(_initial(success, status=TaskStatus.SUCCESS, artifacts={"no_data": True, "raw_item_ids": [], "card_build_task_ids": []}))
    repo.enqueue(_initial(blocked, status=TaskStatus.BLOCKED))
    repo.enqueue(_initial(pending, status=TaskStatus.PENDING, attempt=3))
    repo.enqueue(Task(task_id="stale-retry", task_type=TaskType.FETCH, target_kind="source", target_id=success["source_id"], status=TaskStatus.FAILED, input_version="stale", artifacts_summary={"initial_fetch": True}))
    repo.enqueue(Task(task_id="summary-valid", task_type=TaskType.SUMMARY, target_kind="card", target_id="card-valid", status=TaskStatus.SUCCESS, input_version="summary-v1", result_version="summary-v1"))
    repo.enqueue(Task(task_id="media-gap", task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card", target_id="card-valid", status=TaskStatus.SKIPPED, artifacts_summary={"gap_reasons": ["x_media_download_browser_session"]}))
    repo.enqueue(Task(task_id="context-gap", task_type=TaskType.CONTEXT_PACK, target_kind="card", target_id="card-valid", status=TaskStatus.SUCCESS, artifacts_summary={"media_gaps": [{"reason": "x_media_download_browser_session"}]}))
    repo.enqueue(Task(task_id="media-benign", task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card", target_id="card-benign", status=TaskStatus.SKIPPED, artifacts_summary={"download_status": "skipped_no_media_expected", "gap_reasons": []}))
    repo.enqueue(Task(task_id="context-benign", task_type=TaskType.CONTEXT_PACK, target_kind="card", target_id="card-benign", status=TaskStatus.SUCCESS, artifacts_summary={"media_gaps": [{"reason": "skipped_no_media_expected"}]}))
    result = read_reliability_status(tmp_path, now="2026-07-13T01:00:00Z")

    assert result["initial_fetch"] == {
        "eligible": 3, "grandfathered": 0, "accounted": 3, "pending": 1, "running": 0, "success": 1,
        "blocked": 1, "failed": 0, "success_rate": 0.3333, "no_data_success": 1,
        "failure_classes_by_platform": {"rss": {"platform_not_logged_in": 1}},
    }
    assert result["product_yield"]["successful_fetch_raw_items"] == 0
    assert "valid_summary_yield" not in result["product_yield"]
    assert result["degradation"] == {"media": 1}


def test_reliability_excludes_grandfathered_supported_source_from_initial_denominator(tmp_path):
    _source(tmp_path, "legacy-supported", status="supported")
    current = _source(tmp_path, "current-initial")
    TaskRepository.for_base_dir(tmp_path).enqueue(_initial(current, status=TaskStatus.PENDING))

    metrics = read_reliability_status(tmp_path)["initial_fetch"]

    assert metrics["eligible"] == 1
    assert metrics["grandfathered"] == 1
    assert metrics["accounted"] == 1
    assert metrics["pending"] == 1
