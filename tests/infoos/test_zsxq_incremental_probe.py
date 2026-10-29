"""Incremental lists and single-request recovery use isolated rows and fake probes."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import replace

import pytest

from backend.application.tasks.execution_clock import add_seconds
from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.planner import plan_fetch_tasks_for_source
from backend.application.tasks.platform_circuits import record_platform_failure
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.zsxq_incremental import apply_incremental_window
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.intelligence.opportunity import is_opportunity_candidate
from backend.domain.models.raw_item import RawItem, RawTimestamps
from backend.domain.sources.configuration import database_source_to_connector_config
from backend.domain.sources.zsxq_request_budget import zsxq_command_timeout
from backend.infrastructure.connectors.opencli import OpenCLICommandError
from backend.infrastructure.storage.opportunity_candidates import WIND_VANE_CANDIDATE
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.sources import SourceRepository
from backend.infrastructure.storage.task_events import TaskEventRepository

NOW = "2026-10-19T00:10:00Z"


def source(base, source_id="topics", kind="topics", identifier="all", group="42", enabled=True):
    row = {"source_id": source_id, "platform": "zsxq", "source_type": kind,
           "identifier": identifier, "display_name": source_id, "cadence_hours": 24,
           "enabled": enabled, "connector_status": "supported",
           "raw_metadata": {"group_id": group, "fetch_rate_policy": {"enabled": False}}}
    SourceRepository(base).upsert_source(row)
    return SourceRepository(base).get_source(source_id)


def raw(base, source_id, published_at, item_id="post"):
    item = RawItem(raw_item_id=item_id, connector_id=f"zsxq:{source_id}", source_platform="zsxq",
                   source_type="article", source_url=f"https://wx.zsxq.com/topic/{item_id}", captured_at=NOW,
                   raw_timestamps=RawTimestamps(published_at=published_at),
                   raw_metadata={"source_id": source_id, "tags": ["风向标"]})
    RawItemRepository(base).upsert_raw_items([item])
    return item


@pytest.mark.parametrize("kind,identifier,expected", [
    ("topics", "all", True), ("opportunity", "all", True),
    ("topics", "ai", False), ("opportunity", "ai", False), ("keyword", "AI", False),
])
def test_incremental_command_uses_source_history_and_one_hour_overlap(tmp_path, kind, identifier, expected):
    row = source(tmp_path, kind=kind, identifier=identifier)
    raw(tmp_path, "topics", "2026-10-19T09:00:00+08:00")
    raw(tmp_path, "topics", "2026-10-19T00:30:00Z", "older")
    raw(tmp_path, "other", "2026-10-20T00:30:00Z", "unrelated")
    task = Task("task", TaskType.FETCH, "source", "topics", artifacts_summary={"scheduled_for": NOW})
    config = database_source_to_connector_config(row)
    apply_incremental_window(tmp_path, row, config, task)
    assert ("--since" in config["command"]) is expected
    if expected:
        assert config["command"][-2:] == ["--since", "2026-10-19T00:00:00Z"]
        assert config["timeout_seconds"] == zsxq_command_timeout(config["command"])


def test_latest_published_at_compares_mixed_offsets_and_preserves_original(tmp_path):
    source(tmp_path)
    latest = "2026-10-19T21:07:28.710+0800"
    raw(tmp_path, "topics", latest, "latest")
    raw(tmp_path, "topics", "2026-10-19T21:07:28.709+08:00", "colon-offset")
    raw(tmp_path, "topics", "2026-10-19T13:07:28.708Z", "utc")
    raw(tmp_path, "topics", "invalid", "invalid")
    raw(tmp_path, "other", "2026-10-20T00:00:00Z", "unrelated")
    assert RawItemRepository(tmp_path).latest_published_at("topics") == latest


def test_incremental_command_uses_compact_offset_and_utc_one_hour_overlap(tmp_path):
    row = source(tmp_path)
    raw(tmp_path, "topics", "2026-10-19T21:07:28.710+0800")
    task = Task("task", TaskType.FETCH, "source", "topics", artifacts_summary={"scheduled_for": NOW})
    config = database_source_to_connector_config(row)
    apply_incremental_window(tmp_path, row, config, task)
    assert config["command"][-2:] == ["--since", "2026-10-19T12:07:28.710000Z"]


def test_no_history_initial_manual_and_backfill_keep_original_command(tmp_path):
    row = source(tmp_path)
    task = Task("task", TaskType.FETCH, "source", "topics", artifacts_summary={"scheduled_for": NOW})
    config = database_source_to_connector_config(row)
    original = config.copy()
    apply_incremental_window(tmp_path, row, config, task)
    assert config == original
    raw(tmp_path, "topics", "invalid")
    assert RawItemRepository(tmp_path).latest_published_at("topics") is None
    raw(tmp_path, "topics", NOW, "valid")
    for artifacts in [{}, {"scheduled_for": NOW, "initial_fetch": True},
                      {"scheduled_for": NOW, "digest_backfill_window": {"limit": 20}}]:
        config = database_source_to_connector_config(row)
        apply_incremental_window(tmp_path, row, config, replace(task, artifacts_summary=artifacts))
        assert "--since" not in config["command"]


def test_incremental_budget_tracks_runtime_page_limit(tmp_path, monkeypatch):
    from backend.infrastructure.connectors.zsxq_runtime import command_timeout
    row = source(tmp_path)
    raw(tmp_path, "topics", NOW)
    task = Task("task", TaskType.FETCH, "source", "topics", artifacts_summary={"scheduled_for": NOW})
    for configured, pages in [("2", 2), ("9", 9), ("invalid", 5), ("0", 5), ("1000", 100)]:
        monkeypatch.setenv("INFOOS_ZSXQ_INCREMENTAL_MAX_PAGES", configured)
        config = database_source_to_connector_config(row)
        apply_incremental_window(tmp_path, row, config, task)
        expected = zsxq_command_timeout(config["command"], incremental_max_pages=pages)
        assert config["timeout_seconds"] == expected == command_timeout(config["command"])


@pytest.mark.parametrize("tags,platform,kind,expected", [
    (["风向标"], "zsxq", "topics", True), (["风向标"], "zsxq", "keyword", True),
    (["风向标"], "x", "topics", False), (["AI 风向标"], "zsxq", "topics", True),
    (["风向"], "zsxq", "topics", False),
    ([{"name": "风向标"}], "zsxq", "topics", False), ("风向标", "zsxq", "topics", False),
    (None, "zsxq", "topics", False), ([], "zsxq", "opportunity", True),
])
def test_tag_candidate_python_and_sql_match_stored_raw_format(tmp_path, tags, platform, kind, expected):
    payload = {"raw_metadata": {"tags": tags}}
    assert is_opportunity_candidate(payload, platform, kind) is expected
    item = RawItem(raw_item_id="candidate", connector_id="zsxq:topics", source_platform=platform,
                   source_type="article", source_url="https://wx.zsxq.com/topic/candidate", captured_at=NOW,
                   raw_metadata=payload["raw_metadata"])
    repository = RawItemRepository(tmp_path)
    repository.upsert_raw_items([item])
    with repository.connect_existing() as conn:
        found = conn.execute(f"""with s(platform, source_type) as (values (?, ?))
            select {WIND_VANE_CANDIDATE} from raw_item_candidate_flags f cross join s
            where f.connector_id=? and f.raw_item_id=?""",
                             (platform, kind, item.connector_id, item.raw_item_id)).fetchone()[0]
    assert bool(found) is expected
    stored = repository.get_for_connector(item.connector_id, item.raw_item_id)
    assert stored["raw_payload"]["raw_metadata"]["tags"] == tags


@pytest.mark.parametrize("topics_enabled,topics_group,topics_tab,opportunity_tab", [
    (True, "42", "all", "all"), (False, "42", "all", "all"),
    (True, "other", "all", "all"), (True, "42", "ai", "all"),
    (True, "42", "all", "ai"),
])
def test_periodic_opportunity_runs_independently(
    tmp_path, topics_enabled, topics_group, topics_tab, opportunity_tab,
):
    from backend.read_models.source_management import read_source_detail
    row = source(tmp_path, "op", "opportunity", opportunity_tab)
    source(tmp_path, group=topics_group, identifier=topics_tab, enabled=topics_enabled)
    assert len(plan_fetch_tasks_for_source(tmp_path, row, now=NOW)) == 1
    projection = read_source_detail(tmp_path, "op")["source"]
    assert projection["next_run_at"] == row["next_run_at"]


@pytest.mark.parametrize("owner,group,enabled,identifier,expected", [
    (False, "42", True, "all", ["tagged", "ordinary", "similar"]),
    (True, "42", True, "all", ["ordinary", "similar"]),
    (True, "other", True, "all", ["tagged", "ordinary", "similar"]),
    (True, "42", False, "all", ["tagged", "ordinary", "similar"]),
    (True, "42", True, "ai", ["tagged", "ordinary", "similar"]),
])
def test_topics_defers_tagged_posts_to_enabled_opportunity_all(
    tmp_path, monkeypatch, owner, group, enabled, identifier, expected,
):
    from backend.application.workflows import acquisition
    from backend.infrastructure.connectors.base import FetchResult
    row = source(tmp_path)
    if owner:
        source(tmp_path, "op", "opportunity", identifier, group, enabled)
    connector_id = database_source_to_connector_config(row)["connector_id"]
    items = [RawItem(raw_item_id=item_id, connector_id=connector_id, source_platform="zsxq",
                     source_type="article", source_url=f"https://wx.zsxq.com/topic/{item_id}",
                     captured_at=NOW, raw_metadata={"tags": tags})
             for item_id, tags in [("tagged", ["风向标"]), ("ordinary", []), ("similar", ["AI 风向标"])]]
    class Connector:
        def fetch_new_items(self, **kwargs):
            return FetchResult(items, "next")
    monkeypatch.setattr(acquisition, "_build_connector", lambda *args: Connector())
    task = Task("fetch", TaskType.FETCH, "source", "topics")
    result = acquisition.fetch_source(tmp_path, source_id="topics", task=task)
    assert result["raw_item_ids"] == expected
    with RawItemRepository(tmp_path).connect_existing() as conn:
        assert [r["raw_item_id"] for r in conn.execute(
            "select raw_item_id from raw_items order by rowid")] == expected


def test_infrastructure_probe_runs_single_command_without_changing_normal_retry_env(monkeypatch):
    from backend.infrastructure.connectors.zsxq_runtime import run_probe
    calls = []
    monkeypatch.setenv("INFOOS_ZSXQ_1059_RETRIES", "2")
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, '[{"status":"ok"}]', '')
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", run)
    run_probe("42")
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[args.index("zsxq"):args.index("zsxq") + 4] == ["zsxq", "probe", "--group_id", "42"]
    assert kwargs["timeout"] == zsxq_command_timeout(["zsxq", "probe"])
    assert kwargs["env"]["INFOOS_ZSXQ_1059_RETRIES"] == "2"


@pytest.mark.parametrize("executor", ["snapshot", "continuous"])
@pytest.mark.parametrize("outcome", ["success", "rate_limited", "timeout", "auth"])
def test_half_open_probe_then_original_task_or_attempt_free_cooldown(tmp_path, monkeypatch, executor, outcome):
    from backend.application.tasks.continuous_task_execution import _execute_claimed
    from backend.application.tasks.continuous_browser import BrowserLifetime
    from backend.application.tasks.lanes import ExecutionLane
    from backend.application.tasks.probe_heartbeat import ProbeHeartbeat
    source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("probe-task", TaskType.FETCH, "source", "topics", attempt=3, max_attempts=4))
    task = repo.get("probe-task")
    record_platform_failure(tmp_path, task, platform_scope="zsxq", owner_token=None,
                            reason=FetchFailureReason.RATE_LIMITED, occurred_at=add_seconds(NOW, -600))
    monkeypatch.setattr("backend.application.tasks.continuous_task_execution.current_iso", lambda: NOW)
    monkeypatch.setattr("backend.application.tasks.zsxq_probe.current_iso", lambda: NOW)
    monkeypatch.setattr("backend.application.tasks.processor_execution.current_iso", lambda: NOW)
    # No actual auth recheck or browser network calls in a classification test.
    monkeypatch.setattr("backend.application.tasks.auth_recheck.recheck_auth_failure", lambda error, *a, **kw: error)
    events = []
    def probe(group):
        assert group == "42"
        assert RuntimeCircuitRepository(tmp_path).get("platform", "zsxq")["state"] == "half_open"
        from backend.application.workflows.browser_acquisition_batch import active_browser_acquisition_batch
        assert active_browser_acquisition_batch() is not None
        events.append("probe")
        message = {"rate_limited": "RATE_LIMITED zsxq risk control 1059 (rate limited)",
                   "timeout": "OpenCLI command timed out", "auth": "auth_required: login required"}.get(outcome)
        if message:
            raise OpenCLICommandError(1, stderr_summary=message)
    def handler(*_):
        events.append("task")
        assert RuntimeCircuitRepository(tmp_path).get("platform", "zsxq")["state"] == "closed"
        return {"status": "success", "artifacts": {}}
    monkeypatch.setattr("backend.application.tasks.zsxq_probe.run_probe", probe)
    handlers = {TaskType.FETCH: handler}
    if executor == "snapshot":
        run_due_tasks(tmp_path, now=NOW, handlers=handlers)
    else:
        claimed = repo.claim_task_ids(["probe-task"], worker_id="test", now=NOW,
                                     lease_expires_at=add_seconds(NOW, 300))[0]
        browser = BrowserLifetime(tmp_path)
        try:
            result, reason = _execute_claimed(claimed, base_path=tmp_path, worker_id="test",
                                             lease_seconds=300, resource=None, handlers=handlers,
                                             lane=ExecutionLane.BROWSER_ACQUISITION, browser=browser)
            assert reason is None
        finally:
            browser.close_if_idle(force=True)
    durable = repo.get("probe-task")
    state = RuntimeCircuitRepository(tmp_path).get("platform", "zsxq")
    if outcome == "success":
        assert events == ["probe", "task"] and durable.status is TaskStatus.SUCCESS
        assert state["state"] == "closed"
    elif outcome == "rate_limited":
        assert events == ["probe"] and durable.attempt == 3 and durable.status is TaskStatus.PENDING
        assert durable.next_run_at == state["cooldown_until"] == add_seconds(NOW, 600)
        assert TaskEventRepository(tmp_path).list_events(event_type="circuit_probe_failed")
    else:
        assert events == ["probe"] and durable.attempt == 4
        assert durable.last_failure_class == ("network_timeout" if outcome == "timeout" else "platform_not_logged_in")
    # A completed probe must stop renewing a half-open row that no longer exists.
    heartbeat = ProbeHeartbeat(tmp_path)
    heartbeat.bind("platform", "zsxq", "unused")
    heartbeat.finish(lambda: None)
    assert heartbeat.renew(NOW)


def test_acquisition_passes_since_and_replays_overlap_to_raw_dedup(tmp_path, monkeypatch):
    from backend.application.workflows.acquisition import fetch_source
    from backend.domain.sources.versioning import source_fetch_input_version
    from backend.infrastructure.connectors.base import FetchResult
    from backend.infrastructure.storage.source_cursors import SourceCursorRepository
    row = source(tmp_path)
    raw(tmp_path, "topics", NOW)
    SourceCursorRepository(tmp_path).upsert(source_id="topics", connector_id=database_source_to_connector_config(row)["connector_id"],
                                           cursor_value="post", last_run_id=None)
    configs, cursors = [], []
    class Fake:
        def fetch_new_items(self, since_cursor):
            cursors.append(since_cursor)
            return FetchResult(items=[], next_cursor="post")
    def build(base, config):
        configs.append(config)
        return Fake()
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", build)
    task = Task("incremental", TaskType.FETCH, "source", "topics", input_version=source_fetch_input_version(row),
                artifacts_summary={"scheduled_for": NOW})
    fetch_source(tmp_path, source_id="topics", task=task)
    assert configs[0]["command"][-2:] == ["--since", "2026-10-18T23:10:00Z"]
    assert "digest_recheck" in configs[0]
    assert cursors == [None]  # The older ID cursor must not truncate the one-hour overlap.


def test_probe_does_not_retry_stale_identity_output(monkeypatch):
    from backend.infrastructure.connectors.zsxq_runtime import run_probe
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, '', 'stale page identity')
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", run)
    with pytest.raises(OpenCLICommandError):
        run_probe("42")
    assert len(calls) == 1
