"""Source lookup failures and X numeric cursor acquisition evidence."""
from __future__ import annotations

import sqlite3
from unittest.mock import Mock

import pytest
from requests import HTTPError, Response

from backend.application.tasks.platform_circuits import record_platform_failure
from backend.application.tasks.processor_execution import persist_task_failure
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.retry import decide_task_retry
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.acquisition import fetch_source
from backend.application.workflows.acquisition_failures import AcquisitionFailureError, classify_acquisition_failure
from backend.application.workflows.fetch_support import build_connector
from backend.domain.failures.connector_diagnostics import classify_connector_diagnostic
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.sources.acquisition import acquisition_artifacts, normalize_acquisition
from backend.domain.sources.configuration import database_source_to_connector_config
from backend.infrastructure.connectors.opencli import OpenCLICommandError, OpenCLIConnector
from backend.infrastructure.connectors.x_timeline import XTimelineConnector
from backend.infrastructure.storage.repositories import RunRepository, SourceCursorRepository, SourceRepository
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository
from backend.read_models.source_stats import get_source_stats
from tests.infoos._factories import make_supported_source_payload


NOW = "2026-10-19T08:00:00Z"
UNAVAILABLE = "X account @example not found or unavailable (Suspended)"


def _failure(message=UNAVAILABLE):
    return classify_acquisition_failure(
        source={"source_platform": "x"}, result=None,
        error=OpenCLICommandError(1, stderr_summary=message),
    )


def test_explicit_x_source_failure_is_non_retryable_and_blocked():
    diagnostic = classify_connector_diagnostic([{"message": UNAVAILABLE}], platform="x")
    assert diagnostic["category"] == "source_unavailable"
    assert diagnostic["retryable"] is False
    assert classify_connector_diagnostic([{"message": UNAVAILABLE}], platform="rss")["category"] != "source_unavailable"
    failure = _failure()
    assert failure.reason is FetchFailureReason.SOURCE_UNAVAILABLE
    assert failure.retryable is False
    assert "modify or disable this source" in failure.user_action
    decision = decide_task_retry(Task("fetch", TaskType.FETCH, "source", "x"), reason=failure.reason, now=NOW)
    assert decision.status is TaskStatus.BLOCKED
    assert decision.next_run_at is None


@pytest.mark.parametrize("message,reason", [
    ("Could not resolve @x", FetchFailureReason.OPENCLI_COMMAND_FAILED),
    ("HTTP 429: UserByScreenName fetch failed — rate-limited by Twitter", FetchFailureReason.RATE_LIMITED),
    ("HTTP 401: UserByScreenName fetch failed — auth failed (cookie expired)", FetchFailureReason.PLATFORM_NOT_LOGGED_IN),
    ("UserByScreenName API error: Invalid query", FetchFailureReason.OPENCLI_COMMAND_FAILED),
    ("HTTP 404: UserByScreenName fetch failed — resource not found", FetchFailureReason.OPENCLI_COMMAND_FAILED),
])
def test_ambiguous_and_api_failures_keep_platform_classification(message, reason):
    assert _failure(message).reason is reason


@pytest.mark.parametrize("status", [404, 410, 500])
def test_rss_http_status_is_read_from_response_without_response_truthiness(status):
    # requests.Response is false for 4xx/5xx, so never use `if response`.
    response = Response()
    response.status_code = status
    error = HTTPError(f"{status} Server Error", response=response)
    source = database_source_to_connector_config(make_supported_source_payload())
    assert source["type"] == "rss"
    assert source.get("source_platform") is None
    assert source.get("platform") is None
    failure = classify_acquisition_failure(source=source, result=None, error=error)
    if status in {404, 410}:
        assert failure.reason is FetchFailureReason.SOURCE_UNAVAILABLE
        assert failure.retryable is False
        assert "feed URL" in failure.user_action
    else:
        assert failure.reason is FetchFailureReason.OPENCLI_COMMAND_FAILED
        assert failure.retryable is True


def test_rss_timeout_keeps_existing_classification():
    source = database_source_to_connector_config(make_supported_source_payload())
    failure = classify_acquisition_failure(source=source, result=None, error=TimeoutError("feed timed out"))
    assert failure.reason is FetchFailureReason.NETWORK_TIMEOUT


@pytest.mark.parametrize("status", [404, 410])
def test_rss_fetch_unavailable_blocks_task_without_platform_failure(tmp_path, monkeypatch, status):
    source = make_supported_source_payload()
    SourceRepository(tmp_path).upsert_source(source)
    response = Response()
    response.status_code = status
    response.url = source["identifier"]
    get = Mock(return_value=response)
    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", get)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("fetch-rss", TaskType.FETCH, "source", source["source_id"]))
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute("update tasks set status = 'running', claimed_by = 'worker' where task_id = 'fetch-rss'")
    task = repo.get("fetch-rss")
    circuits = RuntimeCircuitRepository(tmp_path)
    assert circuits.get("platform", "rss") is None

    with pytest.raises(AcquisitionFailureError) as caught:
        fetch_source(tmp_path, source_id=source["source_id"], task=task)

    get.assert_called_once()
    assert get.call_args.args == (source["identifier"],)
    assert isinstance(caught.value.__cause__, HTTPError)
    assert caught.value.failure.reason is FetchFailureReason.SOURCE_UNAVAILABLE
    persist_task_failure(repo, task, worker_id="worker", now=NOW, error=caught.value,
                         artifacts={}, base_path=tmp_path, platform_scope="rss")
    task = repo.get(task.task_id)
    assert task.status is TaskStatus.BLOCKED
    assert task.last_failure_class == "source_unavailable"
    assert task.next_run_at is None
    assert circuits.get("platform", "rss") is None


@pytest.mark.parametrize("half_open", [False, True])
def test_source_failure_preserves_platform_health_and_releases_probe(tmp_path, half_open):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("fetch", TaskType.FETCH, "source", "x"))
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute("update tasks set status = 'running', claimed_by = 'worker' where task_id = 'fetch'")
    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(failure_threshold=1 if half_open else 3, cooldown_seconds=1)
    circuits.record_failure("platform", "x", occurred_at="2026-10-19T07:00:00Z", policy=policy)
    if half_open:
        assert circuits.admit("platform", "x", owner_token="probe", now=NOW).probe
    before = circuits.get("platform", "x")
    record_platform_failure(tmp_path, repo.get("fetch"), platform_scope="x", owner_token="probe",
                            reason=FetchFailureReason.SOURCE_UNAVAILABLE, occurred_at=NOW)
    assert circuits.get("platform", "x") == before

    persist_task_failure(repo, repo.get("fetch"), worker_id="worker", now=NOW,
                         error=AcquisitionFailureError(_failure()), artifacts={}, base_path=tmp_path,
                         platform_scope="x", platform_circuit_owner="probe" if half_open else None)

    task = repo.get("fetch")
    assert task.status is TaskStatus.BLOCKED
    assert task.last_failure_class == "source_unavailable"
    assert task.next_run_at is None
    after = circuits.get("platform", "x")
    assert after["state"] == before["state"] == ("half_open" if half_open else "closed")
    assert after["consecutive_failures"] == before["consecutive_failures"]
    assert after["reason_code"] == before["reason_code"]
    assert after["probe_owner_token"] is None
    assert after["probe_lease_expires_at"] is None
    if half_open:
        assert circuits.admit("platform", "x", owner_token="next-probe", now=NOW).probe


def _connector(monkeypatch, ids, *, limit=3):
    connector = XTimelineConnector(connector_id="x", command=["twitter", "tweets", "example", "--limit", str(limit)],
                                   source_platform="x", source_type="social_post", result_order="newest_first",
                                   use_browser_window=False)
    monkeypatch.setattr(connector, "_load_payload", lambda: [
        {"id": id, "text": f"Tweet {id}", "url": f"https://x.com/example/status/{id}"} for id in ids
    ])
    return connector


@pytest.mark.parametrize("ids,cursor,limit,selected,next_cursor,partial", [
    (["101", "105", "99", "103"], "100", 4, ["101", "105", "103"], "105", False),
    (["105", "103", "99"], "100", 3, ["105", "103"], "105", False),
    (["105", "101", "103"], "100", 3, ["105", "101", "103"], "105", True),
    (["105", "103"], "100", 3, ["105", "103"], "105", False),
    (["99", "100"], "100", 2, [], "100", False),
    (["99", "98"], "100", 2, [], "100", False),
    (["opaque", "101", "99"], "100", 3, ["opaque", "101"], "101", False),
    (["opaque"], "100", 1, ["opaque"], "100", False),
    ([], "100", 3, [], "100", False),
    (["9000000000000000011", "9000000000000000009"], "9000000000000000010", 2,
     ["9000000000000000011"], "9000000000000000011", False),
])
def test_x_numeric_cursor(monkeypatch, ids, cursor, limit, selected, next_cursor, partial):
    result = _connector(monkeypatch, ids, limit=limit)._fetch_new_items_in_lease(cursor)
    assert [item.raw_item_id for item in result.items] == selected
    assert result.next_cursor == next_cursor
    assert result.new_count == len(selected)
    assert result.visible_count == len(ids)
    assert result.dedup_skipped_count == len(ids) - len(selected)
    if partial:
        assert result.acquisition == normalize_acquisition(result.acquisition)
        assert result.acquisition == {"requested_count": limit, "observed_count": len(ids), "complete": False,
                                      "stop_reason": "cursor_not_reached", "stop_position": None,
                                      "stop_message": f"{min(map(int, ids))} 晚于游标 {cursor}，两轮之间可能有推文未抓到"}
        assert acquisition_artifacts(result.acquisition)["completeness_status"] == "partial"
    else:
        assert result.acquisition is None


@pytest.mark.parametrize("cursor,expected", [(None, ["new", "old", "older"]), ("old", ["new"]),
                                                  ("missing-legacy", ["new", "old", "older"])])
def test_legacy_or_empty_cursor_uses_parent_logic(monkeypatch, cursor, expected):
    result = _connector(monkeypatch, ["new", "old", "older"])._fetch_new_items_in_lease(cursor)
    assert [item.raw_item_id for item in result.items] == expected
    assert result.next_cursor == "new"
    assert result.acquisition is None


@pytest.mark.parametrize("platform,order,expected", [("x", "newest_first", XTimelineConnector),
                                                    ("x", "snapshot", OpenCLIConnector),
                                                    ("reddit", "newest_first", OpenCLIConnector)])
def test_connector_selection_is_limited_to_x_newest_first(tmp_path, platform, order, expected):
    connector = build_connector(tmp_path, {"type": "opencli", "connector_id": "test", "command": ["twitter", "tweets"],
                                          "source_platform": platform, "result_order": order})
    assert type(connector) is expected


def test_x_fetch_persists_cursor_gap_in_run_history_and_stats(tmp_path, monkeypatch):
    source = make_supported_source_payload(source_id="x", connector_id="x", platform="x", source_type="creator",
                                           identifier="example", raw_metadata={"max_items_per_run": 3})
    SourceRepository(tmp_path).upsert_source(source)
    SourceCursorRepository(tmp_path).upsert(source_id="x", connector_id="x", cursor_value="100", updated_at=NOW, last_run_id=None)
    monkeypatch.setattr("backend.application.workflows.acquisition._ensure_personalized_feed_login", lambda *_a, **_k: None)
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.OpenCLIConnector.fetch_new_items",
                        lambda self, since_cursor: self._fetch_new_items_in_lease(since_cursor))
    monkeypatch.setattr(OpenCLIConnector, "_load_payload", lambda self: [
        {"id": str(id), "text": f"Tweet {id}", "url": f"https://x.com/example/status/{id}"} for id in [105, 101, 103]
    ])
    monkeypatch.setattr("backend.application.workflows.acquisition.utc_now_seconds_z", lambda: NOW)
    task = Task("fetch-x-gap", TaskType.FETCH, "source", "x")
    artifacts = fetch_source(tmp_path, source_id="x", task=task)
    assert artifacts["completeness_status"] == "partial"
    assert artifacts["stop_reason"] == "cursor_not_reached"
    payload = RunRepository(tmp_path).get_run(task.task_id)["raw_payload"]
    assert payload["completeness_status"] == "partial"
    assert payload["stop_reason"] == "cursor_not_reached"
    assert SourceCursorRepository(tmp_path).get("x")["cursor_value"] == "105"
    stats = get_source_stats(tmp_path, "2026-10-19T00:00:00Z", "2026-10-20T00:00:00Z")["items"][0]
    assert stats["partial_run_count"] == 1
    assert stats["latest_stop_reason"] == "cursor_not_reached"
