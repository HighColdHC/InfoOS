from backend.application.tasks.retry import decide_task_retry
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.failures.taxonomy import FetchFailureReason


def test_network_timeout_uses_policy_backoff():
    decision = decide_task_retry(
        Task(
            task_id="network-timeout",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source-1",
            attempt=1,
            max_attempts=3,
        ),
        reason=FetchFailureReason.NETWORK_TIMEOUT,
        now="2026-07-10T00:00:00Z",
    )

    assert decision.status == TaskStatus.PENDING
    assert decision.attempt == 2
    assert decision.next_run_at == "2026-07-10T00:00:30Z"
    assert decision.failure_class == FetchFailureReason.NETWORK_TIMEOUT.value


def test_retry_decision_escalates_rate_limit_to_blocked():
    decision = decide_task_retry(
        Task(
            task_id="rate-limited",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source-1",
            attempt=3,
            max_attempts=3,
        ),
        reason=FetchFailureReason.RATE_LIMITED,
        now="2026-07-10T00:00:00Z",
    )

    assert decision.status == TaskStatus.BLOCKED
    assert decision.attempt == 4
    assert decision.next_run_at is None


def test_auth_required_becomes_blocked_without_immediate_retry():
    from backend.application.tasks.retry import classify_task_failure
    from backend.application.workflows.acquisition_failures import AcquisitionFailure, AcquisitionFailureError

    task = Task(
        task_id="auth-blocked",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
    )
    error = AcquisitionFailureError(
        AcquisitionFailure(
            reason=FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
            message="login required",
            retryable=False,
            user_action="Log in",
            diagnostic={"category": "auth_required"},
        )
    )
    decision = decide_task_retry(
        task,
        reason=classify_task_failure(task, error),
        now="2026-07-10T00:00:00Z",
    )

    assert decision.status == TaskStatus.BLOCKED
    assert decision.next_run_at is None
