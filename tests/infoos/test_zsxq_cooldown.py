"""Natural-cooling contracts use fake timestamps and isolated temporary state."""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace

import pytest

from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.platform_circuits import record_platform_failure, zsxq_cooldown_retry_at
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.retry import decide_task_retry
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.acquisition_failures import (
    AcquisitionFailureError, classify_acquisition_failure,
)
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.sources.zsxq_request_budget import zsxq_command_timeout
from backend.infrastructure.connectors.opencli import OpenCLICommandError, OpenCLIConnector
from backend.infrastructure.connectors.research import ResearchConnector
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository


NOW = "2026-10-19T00:00:00Z"


def _failure(message="zsxq risk control 1059 (rate limited)"):
    return classify_acquisition_failure(
        source={"platform": "zsxq"}, result=None,
        error=OpenCLICommandError(1, stderr_summary=message),
    )


@pytest.mark.parametrize("message", ["code 1059", "不支持非官方工具访问", "RATE_LIMITED",
                                     "zsxq risk control 1059 (rate limited)"])
def test_diagnostic_classifies_zsxq_risk_control_as_rate_limited(message):
    failure = _failure(message)
    assert failure.reason is FetchFailureReason.RATE_LIMITED
    assert failure.retryable
    assert failure.diagnostic["category"] == "rate_limited"
    assert "自然冷却" in failure.user_action
    other = classify_acquisition_failure(source={"platform": "x"}, result=None,
                                        error=OpenCLICommandError(1, stderr_summary=message))
    if message in {"code 1059", "不支持非官方工具访问", "RATE_LIMITED"}:
        assert other.reason is FetchFailureReason.OPENCLI_COMMAND_FAILED


def test_backoff_is_60_300_1800_and_zsxq_x_exhaust_as_failed():
    task = Task("rate", TaskType.FETCH, "source", "source", max_attempts=4)
    for attempt, deadline in [(1, "00:01:00"), (2, "00:05:00"), (3, "00:30:00")]:
        decision = decide_task_retry(replace(task, attempt=attempt),
                                     reason=FetchFailureReason.RATE_LIMITED, now=NOW, platform="zsxq")
        assert decision.status is TaskStatus.PENDING
        assert decision.next_run_at == f"2026-10-19T{deadline}Z"
    for platform, status in [("zsxq", TaskStatus.FAILED), ("x", TaskStatus.FAILED), ("youtube", TaskStatus.BLOCKED)]:
        decision = decide_task_retry(replace(task, attempt=4),
                                     reason=FetchFailureReason.RATE_LIMITED, now=NOW, platform=platform)
        assert decision.status is status
        assert decision.next_run_at is None


def _source(base, source_id, platform="zsxq", research=False):
    SourceRepository(base).upsert_source({
        "source_id": source_id, "platform": platform,
        "source_type": "keyword", "identifier": "AI", "display_name": source_id,
        "cadence_hours": 24, "enabled": True, "connector_status": "supported",
        "raw_metadata": {"fetch_rate_policy": {"enabled": False},
                         **({"source_scope": "research"} if research else {})},
    })


def test_one_failure_immediately_opens_600s_automatic_circuit_and_single_half_open_probe(tmp_path):
    task = Task("rate", TaskType.FETCH, "source", "source")
    record_platform_failure(tmp_path, task, platform_scope="zsxq", owner_token=None,
                            reason=FetchFailureReason.RATE_LIMITED, occurred_at=NOW)
    circuits = RuntimeCircuitRepository(tmp_path)
    row = circuits.get("platform", "zsxq")
    assert row["state"] == "open"
    assert row["recovery_mode"] == "automatic"
    assert row["cooldown_until"] == "2026-10-19T00:10:00Z"
    assert zsxq_cooldown_retry_at(tmp_path, owner_token="stale", now="2026-10-19T00:10:00Z") == "2026-10-19T00:10:00Z"
    assert not circuits.admit("platform", "zsxq", owner_token="a", now="2026-10-19T00:09:59Z").allowed
    admission = circuits.admit("platform", "zsxq", owner_token="a", now="2026-10-19T00:10:00Z")
    assert admission.allowed and admission.probe and admission.state == "half_open"
    assert not circuits.admit("platform", "zsxq", owner_token="b", now="2026-10-19T00:10:00Z").allowed
    assert zsxq_cooldown_retry_at(tmp_path, owner_token="a", now="2026-10-19T00:10:00Z") is None
    circuits.record_success("platform", "zsxq", occurred_at="2026-10-19T00:10:01Z", owner_token="a")
    assert circuits.get("platform", "zsxq")["state"] == "closed"
    record_platform_failure(tmp_path, task, platform_scope="x", owner_token=None,
                            reason=FetchFailureReason.RATE_LIMITED, occurred_at=NOW)
    assert circuits.get("platform", "x")["state"] == "open"
    assert circuits.get("platform", "x")["cooldown_until"] == "2026-10-19T00:30:00Z"


def test_cooldown_defers_periodic_and_research_fetch_without_attempts_and_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.application.tasks.zsxq_probe.run_probe", lambda *_: None)
    _source(tmp_path, "periodic")
    _source(tmp_path, "research", research=True)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("fails", TaskType.FETCH, "source", "periodic", max_attempts=4))

    def fail(*_):
        raise AcquisitionFailureError(_failure())

    run_due_tasks(tmp_path, now=NOW, handlers={TaskType.FETCH: fail})
    repo.enqueue(Task("research", TaskType.FETCH, "source", "research", max_attempts=4))
    calls = []
    handler = lambda task, *_: calls.append(task.task_id) or {"status": "success", "artifacts": {}}
    run_due_tasks(tmp_path, now="2026-10-19T00:01:00Z", handlers={TaskType.FETCH: handler})
    assert calls == []
    for task_id, attempt in [("fails", 2), ("research", 1)]:
        task = repo.get(task_id)
        assert task.status is TaskStatus.PENDING and task.attempt == attempt
        assert task.next_run_at == "2026-10-19T00:10:00Z"
    run_due_tasks(tmp_path, now="2026-10-19T00:10:00Z", handlers={TaskType.FETCH: handler})
    assert len(calls) == 1
    assert RuntimeCircuitRepository(tmp_path).get("platform", "zsxq")["state"] == "closed"


def test_queued_zsxq_in_same_snapshot_does_not_request_after_first_failure(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    for name in ["first", "second"]:
        _source(tmp_path, name)
        repo.enqueue(Task(name, TaskType.FETCH, "source", name, max_attempts=4))
    calls = []

    def fail(task, *_):
        calls.append(task.task_id)
        raise AcquisitionFailureError(_failure())

    run_due_tasks(tmp_path, now=NOW, handlers={TaskType.FETCH: fail}, max_workers=1)
    assert len(calls) == 1
    waiting = repo.get(next(iter({"first", "second"} - set(calls))))
    assert waiting.attempt == 1 and waiting.next_run_at == "2026-10-19T00:10:00Z"


def test_exhausted_zsxq_failure_has_readable_reason(tmp_path):
    _source(tmp_path, "periodic")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("exhausted", TaskType.FETCH, "source", "periodic", attempt=4, max_attempts=4))

    def fail(*_):
        raise AcquisitionFailureError(_failure())

    run_due_tasks(tmp_path, now=NOW, handlers={TaskType.FETCH: fail})
    task = repo.get("exhausted")
    assert task.status is TaskStatus.FAILED
    assert "自动重试已耗尽" in task.error_reason


def test_zsxq_research_runs_one_command_with_shared_keywords_and_budget(monkeypatch):
    from backend.domain.models.raw_item import RawItem
    from backend.infrastructure.connectors.base import FetchResult

    connector = ResearchConnector(connector_id="research:zsxq", platform="zsxq", source_id="source",
                                  research_batch_id="batch", keywords=["AI", "小红书"], date_range=None,
                                  max_items=100, options={"content_type": "essence"})
    configs = []
    item = RawItem(raw_item_id="1", connector_id="research:zsxq", source_platform="zsxq",
                   source_type="article", source_url="https://wx.zsxq.com/topic/1", captured_at=NOW,
                   raw_text="AI 内容", raw_metadata={"tags": ["小红书"]})

    class Fake:
        def fetch_new_items(self, since_cursor):
            return FetchResult(items=[item], next_cursor=None)

    monkeypatch.setattr(connector, "_opencli_connector", lambda config: configs.append(config) or Fake())
    result = connector.fetch_new_items(None)
    assert len(configs) == 1
    command = configs[0]["command"]
    assert json.loads(command[command.index("--keywords-json") + 1]) == connector.keywords
    assert command[command.index("--limit") + 1] == "100"
    assert result.items[0].raw_metadata["matched_keywords"] == ["AI", "小红书"]


def test_timeout_budget_covers_list_pages_cooling_hydration_and_environment(monkeypatch):
    command = ["zsxq", "search", "AI", "--limit", "100", "--content-type", "essence"]
    # 42 initial requests + up to 3 retries, 15s XHR + 2.5s pacing, 60s
    # cooling, 540s hydration and 60s navigation/output margin.
    assert zsxq_command_timeout(command) == 1448
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setenv("INFOOS_ZSXQ_REQUEST_INTERVAL_MS", "5000")
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", run)
    connector = OpenCLIConnector(connector_id="zsxq", source_platform="zsxq", command=command)
    connector._load_payload()
    assert calls[0]["timeout"] == 1605
    assert calls[0]["env"]["OPENCLI_BROWSER_COMMAND_TIMEOUT"] == "1605"
