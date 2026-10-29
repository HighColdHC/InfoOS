import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.retry import classify_task_failure, decide_task_retry
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.retry_eligibility import is_task_retry_eligible
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.read_models.task_manager import read_task_list
from backend.read_models.source_run_trace_safety import task_identifier_alias


NOW = "2026-07-10T00:00:00Z"


def _task(attempt: int, max_attempts: int = 10) -> Task:
    return Task(
        task_id="retry-contract",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
        attempt=attempt,
        max_attempts=max_attempts,
    )


@pytest.mark.parametrize(
    ("reason", "delays", "terminal_status"),
    [
        (FetchFailureReason.CHROME_NOT_CONFIGURED, (), TaskStatus.BLOCKED),
        (FetchFailureReason.OPENCLI_DAEMON_DOWN, (0,), TaskStatus.FAILED),
        (FetchFailureReason.OCR_FAILED, (30, 30), TaskStatus.FAILED),
        (FetchFailureReason.NETWORK_TIMEOUT, (30, 30, 30), TaskStatus.FAILED),
    ],
)
def test_policy_cap_allows_exactly_zero_to_three_additional_retries(
    reason, delays, terminal_status
):
    for attempt, delay in enumerate(delays, start=1):
        decision = decide_task_retry(_task(attempt), reason=reason, now=NOW)
        assert decision.status == TaskStatus.PENDING
        assert decision.next_run_at == (
            NOW if delay == 0 else f"2026-07-10T00:00:{delay:02d}Z"
        )

    exhausted = decide_task_retry(_task(len(delays) + 1), reason=reason, now=NOW)
    assert exhausted.status == terminal_status
    assert exhausted.next_run_at is None


def test_task_total_execution_cap_can_exhaust_before_policy_cap():
    first_failure = decide_task_retry(
        _task(1, max_attempts=2),
        reason=FetchFailureReason.OPENCLI_COMMAND_FAILED,
        now=NOW,
    )
    exhausted = decide_task_retry(
        _task(2, max_attempts=2),
        reason=FetchFailureReason.OPENCLI_COMMAND_FAILED,
        now=NOW,
    )

    assert first_failure.status == TaskStatus.PENDING
    assert first_failure.next_run_at == "2026-07-10T00:00:30Z"
    assert exhausted.status == TaskStatus.FAILED
    assert exhausted.next_run_at is None


def test_opencli_command_failed_indexes_variable_delays_across_attempts():
    expected_next_runs = (
        "2026-07-10T00:00:30Z",
        "2026-07-10T00:02:00Z",
        "2026-07-10T00:10:00Z",
    )

    for attempt, expected_next_run in enumerate(expected_next_runs, start=1):
        decision = decide_task_retry(
            _task(attempt, max_attempts=4),
            reason=FetchFailureReason.OPENCLI_COMMAND_FAILED,
            now=NOW,
        )

        assert decision.status == TaskStatus.PENDING
        assert decision.next_run_at == expected_next_run


def test_data_export_failure_never_uses_opencli_failure_class():
    task = Task("export-failure", TaskType.DATA_EXPORT, "export", "export-1")

    assert classify_task_failure(task, OSError("disk full")) == FetchFailureReason.DATA_EXPORT_FAILED


def test_normal_task_retry_projection_matches_api_eligibility(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    tasks = (
        Task(
            "retryable-failed",
            TaskType.MEDIA_DOWNLOAD,
            "card",
            "card-1",
            status=TaskStatus.FAILED,
            attempt=1,
            max_attempts=3,
        ),
        Task(
            "retryable-blocked",
            TaskType.MEDIA_DOWNLOAD,
            "card",
            "card-2",
            status=TaskStatus.BLOCKED,
            attempt=2,
            max_attempts=3,
        ),
        Task(
            "exhausted-failed",
            TaskType.MEDIA_DOWNLOAD,
            "card",
            "card-3",
            status=TaskStatus.FAILED,
            attempt=3,
            max_attempts=3,
        ),
        Task(
            "skipped-branch",
            TaskType.MEDIA_DOWNLOAD,
            "card",
            "card-4",
            status=TaskStatus.SKIPPED,
            attempt=1,
            max_attempts=3,
        ),
    )
    repo.enqueue_many(list(tasks))

    projected = {
        row["task_id"]: row["retryable"]
        for row in read_task_list(tmp_path, limit=20)
    }

    assert projected == {
        task.task_id: is_task_retry_eligible(task)
        for task in tasks
    }


def test_task_retry_projection_ignores_exhausted_and_skipped_tasks(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    chain = {"chain_id": "chain-exhausted", "chain_root_task_id": "fetch-exhausted"}
    repo.enqueue(Task(
        "fetch-exhausted",
        TaskType.FETCH,
        "source",
        "source-exhausted",
        status=TaskStatus.FAILED,
        attempt=3,
        max_attempts=3,
        artifacts_summary=chain,
    ))
    repo.enqueue(Task(
        "media-skipped",
        TaskType.MEDIA_DOWNLOAD,
        "card",
        "card-exhausted",
        status=TaskStatus.SKIPPED,
        artifacts_summary=chain,
    ))

    projected = read_task_list(tmp_path, limit=20, chain_id="chain-exhausted")

    assert len(projected) == 2
    assert all(row["retryable"] is False for row in projected)


def test_task_projection_preserves_explicit_no_speech_transcription_skip(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    chain = {"chain_id": "chain-no-speech", "chain_root_task_id": "fetch-no-speech"}
    repo.enqueue(Task(
        "fetch-no-speech",
        TaskType.FETCH,
        "source",
        "source-no-speech",
        status=TaskStatus.SUCCESS,
        artifacts_summary=chain,
    ))
    repo.enqueue(Task(
        "transcribe-no-speech",
        TaskType.VIDEO_TRANSCRIBE,
        "asset",
        "asset-no-speech",
        status=TaskStatus.SKIPPED,
        artifacts_summary={**chain, "transcript_not_applicable": True},
    ))

    projected = {row["task_id"]: row for row in read_task_list(tmp_path, chain_id="chain-no-speech")}

    assert projected["fetch-no-speech"]["status"] == "success"
    assert projected["transcribe-no-speech"]["status"] == "skipped"
    assert projected["transcribe-no-speech"]["artifacts_summary"]["transcript_not_applicable"] is True
    assert all(row["retryable"] is False for row in projected.values())


def test_manual_retry_accepts_only_retryable_failed_branch_and_preserves_events(tmp_path):
    task = Task(
        task_id="failed-media-branch",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card-1",
        status=TaskStatus.FAILED,
        attempt=1,
        max_attempts=3,
        error_reason="network timeout",
        artifacts_summary={"chain_id": "chain-1"},
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(task)
    events = TaskEventRepository(tmp_path)
    events.append(
        task_id=task.task_id,
        chain_id="chain-1",
        event_type="failed",
        status="failed",
        failure_class="media_download_failed",
        payload={"attempt": 1, "error": "network timeout"},
        occurred_at="2026-07-10T00:00:00Z",
    )
    client = TestClient(create_app(tmp_path))

    response = client.post(f"/api/tasks/{task_identifier_alias(task.task_id)}/retry")

    assert response.status_code == 200
    updated = TaskRepository.for_base_dir(tmp_path).get(task.task_id)
    assert updated.status == TaskStatus.PENDING
    assert updated.attempt == 2
    history = events.list_events(task_id=task.task_id)
    assert [(event["event_type"], event["status"]) for event in history] == [
        ("failed", "failed"),
        ("retry_scheduled", "pending"),
    ]
    assert history[0]["payload"]["error"] == "network timeout"
    assert history[1]["payload"] == {
        "previous_status": "failed",
        "previous_attempt": 1,
        "attempt": 2,
        "trigger": "manual",
    }

    skipped = Task(
        "skipped-media-branch", TaskType.MEDIA_DOWNLOAD, "card", "card-2",
        TaskStatus.SKIPPED, max_attempts=3,
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(skipped)
    denied = client.post(f"/api/tasks/{task_identifier_alias(skipped.task_id)}/retry")
    assert denied.status_code == 409
    assert denied.json() == {
        "code": "task_not_retryable",
        "message": "Only failed or blocked tasks with remaining attempts can be retried",
    }
