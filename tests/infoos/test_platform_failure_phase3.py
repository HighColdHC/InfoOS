from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace

import pytest

from backend.application.tasks.handlers.acquisition import handle_fetch
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.retry import decide_task_retry
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks
from backend.application.workflows.acquisition_failures import (
    AcquisitionFailureError,
    classify_acquisition_failure,
)
from backend.domain.failures.retry_policy import get_retry_policy
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository


DOUYIN_MESSAGE = (
    "OpenCLI command failed with exit code 1: Douyin search did not render result cards "
    "within the timeout. Open the same search in Chrome and verify login/security state before retrying."
)
REDDIT_HTML_MESSAGE = (
    "OpenCLI command failed with exit code 1: SyntaxError: Unexpected token '<', "
    '"<!DOCTYPE html><body>" is not valid JSON; trace=/tmp/opencli/reddit.trace'
)


def _failure(platform: str, message: str, *, error: BaseException | None = None, raw_ref: str | None = None):
    item = {"message": message}
    if raw_ref:
        item["raw_response_ref"] = raw_ref
    return classify_acquisition_failure(
        source={"platform": platform, "source_type": "keyword"},
        result={"errors": [item]},
        error=error,
    )


def test_reddit_structured_diagnostic_distinguishes_explicit_auth_from_generic_html():
    auth = _failure("reddit", "Reddit whoami returned HTTP 403 Forbidden; login required")
    generic = _failure(
        "reddit",
        REDDIT_HTML_MESSAGE,
        error=TimeoutError("generic exception text says timeout"),
        raw_ref="/tmp/opencli/reddit-response.html",
    )

    assert auth is not None
    assert auth.reason == FetchFailureReason.PLATFORM_NOT_LOGGED_IN
    assert auth.retryable is False
    assert "Log in to Reddit" in str(auth.user_action)
    assert generic is not None
    assert generic.reason == FetchFailureReason.PARSE_FAILED
    assert generic.retryable is False
    assert generic.user_action == "Inspect connector output mapping before retrying."
    assert generic.diagnostic["raw_response_ref"] == "/tmp/opencli/reddit-response.html"


@pytest.mark.parametrize(
    "failure",
    [
        _failure("reddit", "Reddit whoami returned HTTP 403 Forbidden"),
        _failure("reddit", REDDIT_HTML_MESSAGE),
    ],
)
def test_reddit_failures_are_durably_blocked_without_retry(tmp_path, failure, monkeypatch):
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: tmp_path / "setup-state.json",
    )
    assert failure is not None
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(task_id=f"reddit-{failure.reason.value}", task_type=TaskType.FETCH, target_kind="source", target_id="reddit"))

    run_due_tasks(
        tmp_path,
        now="2026-07-13T00:00:00Z",
        max_workers=1,
        handlers={TaskType.FETCH: lambda *_: (_ for _ in ()).throw(AcquisitionFailureError(failure))},
    )

    task = repo.get(f"reddit-{failure.reason.value}")
    assert task is not None and task.status == TaskStatus.BLOCKED
    assert task.attempt == 2
    assert task.last_failure_class == failure.reason.value
    assert task.next_run_at is None
    assert task.artifacts_summary["failure_reason"] == failure.reason.value
    assert task.artifacts_summary["user_action"] == failure.user_action


def test_douyin_generic_render_failure_is_distinct_from_challenge_and_has_bounded_policy():
    failure = _failure("douyin", DOUYIN_MESSAGE, error=TimeoutError("operation timed out"))

    assert failure is not None
    assert failure.reason == FetchFailureReason.BROWSER_SESSION_RENDER_BLOCKED
    assert failure.diagnostic["evidence"] == "douyin_render_blocked"
    assert failure.user_action == "Inspect the Douyin result page and connector mapping before retrying this source."
    task = Task(task_id="douyin", task_type=TaskType.FETCH, target_kind="source", target_id="douyin", max_attempts=10)
    first = decide_task_retry(replace(task, attempt=1), reason=failure.reason, now="2026-07-13T00:00:00Z")
    second = decide_task_retry(replace(task, attempt=2), reason=failure.reason, now="2026-07-13T00:00:30Z")
    terminal = decide_task_retry(replace(task, attempt=3), reason=failure.reason, now="2026-07-13T00:02:30Z")
    assert (first.status, first.next_run_at) == (TaskStatus.PENDING, "2026-07-13T00:00:30Z")
    assert (second.status, second.next_run_at) == (TaskStatus.PENDING, "2026-07-13T00:02:30Z")
    assert (terminal.status, terminal.next_run_at) == (TaskStatus.BLOCKED, None)
    assert get_retry_policy(FetchFailureReason.OPENCLI_DAEMON_DOWN).delays == (0,)
    timeout = _failure("douyin", "ordinary upstream network operation timed out")
    assert timeout is not None and timeout.reason == FetchFailureReason.NETWORK_TIMEOUT


@pytest.mark.parametrize("message", ["检测到验证码中间页", "challenge iframe src=https://rmc.bytedance.com/captcha"])
def test_douyin_challenge_evidence_requires_user_action(message):
    failure = _failure("douyin", message)

    assert failure is not None
    assert failure.reason == FetchFailureReason.PLATFORM_CHALLENGE_REQUIRED
    assert failure.diagnostic["reason"] == "douyin_challenge_required"
    assert failure.user_action == "Open Douyin in the dedicated InfoOS browser profile and complete the security verification."


def test_douyin_render_failure_persists_two_delays_then_actionable_block(tmp_path):
    failure = _failure("douyin", DOUYIN_MESSAGE, error=TimeoutError("operation timed out"))
    assert failure is not None
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(task_id="douyin-durable", task_type=TaskType.FETCH, target_kind="source", target_id="douyin", max_attempts=10))
    handler = {TaskType.FETCH: lambda *_: (_ for _ in ()).throw(AcquisitionFailureError(failure))}

    run_due_tasks(tmp_path, now="2026-07-13T00:00:00Z", max_workers=1, handlers=handler)
    first = repo.get("douyin-durable")
    assert first is not None and first.status == TaskStatus.PENDING
    assert first.next_run_at == "2026-07-13T00:00:30Z"
    run_due_tasks(tmp_path, now="2026-07-13T00:00:30Z", max_workers=1, handlers=handler)
    second = repo.get("douyin-durable")
    assert second is not None and second.status == TaskStatus.PENDING
    assert second.next_run_at == "2026-07-13T00:02:30Z"
    run_due_tasks(tmp_path, now="2026-07-13T00:02:30Z", max_workers=1, handlers=handler)

    terminal = repo.get("douyin-durable")
    assert terminal is not None and terminal.status == TaskStatus.BLOCKED
    assert terminal.attempt == 4
    assert terminal.last_failure_class == "browser_session_render_blocked"
    assert terminal.next_run_at is None
    assert terminal.artifacts_summary["failure_reason"] == "browser_session_render_blocked"
    assert terminal.artifacts_summary["user_action"] == failure.user_action


def test_douyin_browser_repair_runs_once_across_delayed_retries(tmp_path, monkeypatch):
    failure = _failure("douyin", DOUYIN_MESSAGE)
    assert failure is not None

    class Batch:
        prepare_result = {"status": "ready"}
        repair_attempts: list[dict] = []
        source_ids = ["douyin"]

    batches = [Batch(), Batch()]
    repair_calls: list[int] = []

    def repair(batch, **_kwargs):
        repair_calls.append(1)
        batch.repair_attempts.append({"attempt": 1})
        return {"status": "ready"}

    monkeypatch.setattr("backend.application.workflows.browser_acquisition_batch.active_browser_acquisition_batch", lambda: batches.pop(0))
    monkeypatch.setattr("backend.application.workflows.browser_acquisition_batch.repair_browser_acquisition_batch", repair)
    monkeypatch.setattr(
        "backend.application.workflows.acquisition.fetch_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AcquisitionFailureError(failure)),
    )
    task = Task(task_id="douyin", task_type=TaskType.FETCH, target_kind="source", target_id="douyin")

    first = handle_fetch(task, tmp_path)
    assert first["status"] == TaskStatus.FAILED.value
    assert first["artifacts"]["browser_repair_attempted"] is True
    with pytest.raises(AcquisitionFailureError):
        handle_fetch(replace(task, artifacts_summary=first["artifacts"]), tmp_path)
    assert len(repair_calls) == 1


@pytest.mark.parametrize(
    ("returncode", "stderr"),
    [
        (1, "BROWSER_CONNECT failed"),
        (1, 'Browser profile "infoos" is not connected'),
        (69, "OpenCLI command exited"),
    ],
)
def test_x_browser_session_media_is_optional_skipped_gap(tmp_path, monkeypatch, returncode, stderr):
    from backend.application.enrichment.media import download, x_session
    from backend.application.enrichment.media.x_downloader import XFallbackResult
    from backend.application.enrichment.media.x_session import XMediaDownloadLifecycleResult

    card_id = f"card-x-{returncode}-{len(stderr)}"
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": card_id,
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/3",
            "source_author": "Example",
            "title": "X post",
            "content": "Text remains usable without optional media.",
            "captured_at": "2026-07-13T00:00:00Z",
            "processed_at": "2026-07-13T00:00:00Z",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.subprocess.run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": returncode, "stdout": "", "stderr": stderr})(),
    )
    command_result = type(
        "Result", (), {"returncode": returncode, "stdout": "", "stderr": stderr}
    )()
    monkeypatch.setattr(
        download,
        "run_x_media_download_lifecycle",
        lambda **_kwargs: XMediaDownloadLifecycleResult(
            command_result=command_result,
            failure_reason="x_media_download_browser_session",
            details={"strategy": "opencli", "exit_code": returncode},
        ),
    )
    monkeypatch.setattr(
        download,
        "run_x_yt_dlp_fallback",
        lambda *_args, **_kwargs: XFallbackResult(
            status="failed",
            files=[],
            reason="x_yt_dlp_inventory_failed",
            provenance={},
            evidence={"strategy": "yt_dlp", "exit_code": returncode},
        ),
    )
    monkeypatch.setattr(x_session, "prepare_opencli_browser_session", lambda **_kwargs: {
        "status": "ready",
        "profile": {"alias": "infoos-fetch", "bound": True, "binding_status": "ready"},
    })
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: None)
    monkeypatch.setattr(x_session, "opencli_browser_lock", nullcontext)
    repo = TaskRepository.for_base_dir(tmp_path)
    task_id = f"media-{card_id}"
    repo.enqueue(Task(task_id=task_id, task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card", target_id=card_id))

    result = run_due_tasks(tmp_path, max_workers=1, task_types=[TaskType.MEDIA_DOWNLOAD])

    task = repo.get(task_id)
    assert result["skipped"] == 1
    assert task is not None and task.status == TaskStatus.SKIPPED and task.attempt == 1
    assert task.artifacts_summary["failure_reason"] == "x_media_download_browser_session"
    assert task.artifacts_summary["user_action"] == "Reconnect the OpenCLI profile, then retry media/card."
    assert task.artifacts_summary["diagnostic_reference"]["exit_code"] == returncode
    assert task.artifacts_summary["gap_reasons"] == ["x_media_download_browser_session"]
    gap = next(block for block in CardContentBlockRepository(tmp_path).list_for_card(card_id) if block["block_kind"] == "media_download_gap")
    assert gap["raw_payload"]["reason"] == "x_media_download_browser_session"
    finalize = repo.list_tasks(task_type=TaskType.CARD_FINALIZE, limit=10)
    assert len(finalize) == 1
    assert finalize[0].artifacts_summary["media_gaps"][0]["reason"] == "x_media_download_browser_session"


def test_unknown_x_media_and_fallback_failure_remains_optional(tmp_path, monkeypatch):
    from backend.application.enrichment.media import download, x_session
    from backend.application.enrichment.media.x_downloader import XFallbackResult
    from backend.application.enrichment.media.x_session import XMediaDownloadLifecycleResult

    card_id = "card-x-unknown"
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": card_id, "source_id": None, "source_platform": "x", "source_type": "social_post",
            "source_url": "https://x.com/example/status/4", "source_author": "Example", "title": "X post",
            "content": "text", "captured_at": "2026-07-13T00:00:00Z", "processed_at": "2026-07-13T00:00:00Z",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.subprocess.run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 1, "stdout": "", "stderr": "unknown download failure"})(),
    )
    command_result = type(
        "Result", (), {"returncode": 1, "stdout": "", "stderr": "unknown download failure"}
    )()
    monkeypatch.setattr(
        download,
        "run_x_media_download_lifecycle",
        lambda **_kwargs: XMediaDownloadLifecycleResult(
            command_result=command_result,
            failure_reason="x_media_download_unknown",
            details={"strategy": "opencli", "exit_code": 1},
        ),
    )
    monkeypatch.setattr(
        download,
        "run_x_yt_dlp_fallback",
        lambda *_args, **_kwargs: XFallbackResult(
            status="failed",
            files=[],
            reason="x_yt_dlp_inventory_failed",
            provenance={},
            evidence={"strategy": "yt_dlp", "exit_code": 1},
        ),
    )
    monkeypatch.setattr(x_session, "prepare_opencli_browser_session", lambda **_kwargs: {
        "status": "ready",
        "profile": {"alias": "infoos-fetch", "bound": True, "binding_status": "ready"},
    })
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: None)
    monkeypatch.setattr(x_session, "opencli_browser_lock", nullcontext)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(task_id="media-x-unknown", task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card", target_id=card_id))

    result = run_due_tasks(tmp_path, max_workers=1, task_types=[TaskType.MEDIA_DOWNLOAD])

    task = repo.get("media-x-unknown")
    assert result["skipped"] == 1
    task = repo.get("media-x-unknown")
    assert task is not None and task.status == TaskStatus.SKIPPED
    assert task.artifacts_summary["gap_reasons"] == ["x_media_download_fallback_failed"]
