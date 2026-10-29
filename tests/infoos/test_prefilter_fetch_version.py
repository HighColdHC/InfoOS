"""Rule upgrades must not make unchanged source results fail at commit."""
from dataclasses import replace

import pytest

from backend.application.tasks.planner import plan_initial_fetch, plan_manual_fetch_task_for_source
from backend.application.tasks.repository import TaskRepository
from backend.application.workflows.acquisition import fetch_source
from backend.application.workflows.fetch_commit import SourceConfigurationChangedError
from backend.domain.sources.versioning import source_fetch_input_version
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import RunRepository, SourceRepository
from tests.infoos.test_douyin_home_feed import source


def upgraded_source(tmp_path, monkeypatch, *, initial=False):
    import backend.domain.sources.monetization_prefilter as rules
    payload = {**source(raw_metadata={"monetization_prefilter": True}),
               "connector_status": "implemented_uncalibrated" if initial else "supported", "connector_id": "dy_feed"}
    with monkeypatch.context() as old:
        old.setattr(rules, "RULES_VERSION", "prefilter_v1")
        SourceRepository(tmp_path).upsert_source(payload)
    stored = SourceRepository(tmp_path).get_source(payload["source_id"])
    assert stored["calibration_input_version"] != source_fetch_input_version(stored)
    planner = plan_initial_fetch if initial else plan_manual_fetch_task_for_source
    task = planner(stored, now="2026-10-14T08:24:55Z")
    assert task.artifacts_summary["source_input_version"] == task.input_version == source_fetch_input_version(stored)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)
    task = repo.claim_task_ids([task.task_id], worker_id="version-fixture", now="2026-10-14T08:24:56Z",
                               lease_expires_at="2026-10-14T09:24:56Z")[0]
    monkeypatch.setattr("backend.application.workflows.acquisition._ensure_personalized_feed_login", lambda *a, **k: None)
    return stored, task


@pytest.mark.parametrize("initial", [False, True])
def test_current_task_commits_with_old_calibration_hash(tmp_path, monkeypatch, initial):
    stored, task = upgraded_source(tmp_path, monkeypatch, initial=initial)

    class Connector:
        prefilter_evidence = {}

        def fetch_new_items(self, since_cursor):
            return FetchResult(items=[RawItem(
                raw_item_id="prefilter_version_item", connector_id=stored["connector_id"],
                source_platform="douyin", source_type="home_feed",
                source_url="https://www.douyin.com/video/1234567890123456",
                captured_at="2026-10-14T08:35:47Z", raw_text="version regression fixture",
            )], next_cursor="version-fixture-cursor")

    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: Connector())
    result = fetch_source(tmp_path, source_id=stored["source_id"], task=task)
    assert result["source_input_version"] == task.input_version
    assert result["raw_item_ids"] == ["prefilter_version_item"]
    assert len(result["card_build_task_ids"]) == 1
    assert RunRepository(tmp_path).get_run(task.task_id) is not None
    updated = SourceRepository(tmp_path).get_source(stored["source_id"])
    assert updated["last_run_at"] == result["committed_last_run_at"]
    assert updated["next_run_at"] == result["committed_next_run_at"]
    assert updated["calibration_input_version"] == task.input_version
    assert updated["raw_metadata"] == stored["raw_metadata"]
    assert updated["connector_status"] == "supported"
    replay = fetch_source(tmp_path, source_id=stored["source_id"], task=task)
    assert replay["replayed"] is True
    assert replay["raw_item_ids"] == result["raw_item_ids"]


@pytest.mark.parametrize("change", ["before_fetch", "during_fetch", "stale_task"])
def test_real_input_changes_still_discard_fetch(tmp_path, monkeypatch, change):
    stored, task = upgraded_source(tmp_path, monkeypatch)
    calls = []

    def edit():
        SourceRepository(tmp_path).upsert_source({**stored, "raw_metadata": {
            **stored["raw_metadata"], "prefilter_demand_min_comments": 3,
        }})

    class Connector:
        prefilter_evidence = {}

        def fetch_new_items(self, since_cursor):
            calls.append(True)
            if change == "during_fetch":
                edit()
            return FetchResult(items=[], next_cursor=None)

    if change == "before_fetch":
        edit()
    if change == "stale_task":
        old = stored["calibration_input_version"]
        task = replace(task, input_version=old, artifacts_summary={**task.artifacts_summary, "source_input_version": old})
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: Connector())
    with pytest.raises(SourceConfigurationChangedError, match="source_configuration_changed"):
        fetch_source(tmp_path, source_id=stored["source_id"], task=task)
    assert len(calls) == (1 if change == "during_fetch" else 0)
    assert RunRepository(tmp_path).get_run(task.task_id) is None
