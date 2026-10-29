"""Batch evidence survives filtering, durable commit and read projections."""
import json
from types import SimpleNamespace

import pytest

from backend.application.workflows.acquisition import fetch_source
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.connectors.opencli import OpenCLIConnector
from backend.infrastructure.storage.repositories import RunRepository
from backend.infrastructure.storage.source_run_json import parse_run_artifacts, parse_run_checkpoint
from backend.infrastructure.storage.source_run_trace_queries_types import SourceRunSummaryRow
from backend.read_models.source_run_trace import project_source_run_summary, _trace_product_status
from tests.infoos.test_acquisition_workflow import _Connector, _item, _seed_source, _task


def evidence(observed=1, requested=40, reason="no_progress"):
    return {"requested_count": requested, "observed_count": observed,
            "complete": observed == requested and reason is None,
            "stop_reason": reason, "stop_position": 1 if reason else None}


@pytest.mark.parametrize("meta", [evidence(), {**evidence(), "stop_message": "Feed stopped"}, evidence(3, 3, None), None, {},
                                  {"requested_count": "bad"}, [], "bad"])
def test_connector_optional_meta_and_prefilter_counts(monkeypatch, meta):
    records = [{"id": "video", "url": "https://example.test/video", "is_ad": True},
               {"is_live": True}, {"id": "other", "url": "https://example.test/other"}]
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                            {"items": records, "acquisition": meta})))
    connector = OpenCLIConnector(connector_id="feed", command=["douyin", "feed"],
                                 source_platform="douyin", result_order="snapshot", use_browser_window=False)
    first = connector.fetch_new_items(None)
    assert first.visible_count == 3
    assert len(first.items) == 2
    assert first.acquisition == (meta if isinstance(meta, dict) and "complete" in meta else None)
    second = connector.fetch_new_items(first.next_cursor)
    assert second.items == []
    assert second.acquisition == first.acquisition


@pytest.mark.parametrize("meta,expected", [(evidence(), "partial"), (evidence(40, 40, None), "complete"), (None, None)])
def test_workflow_persists_optional_acquisition_and_builds_items(tmp_path, monkeypatch, meta, expected):
    _seed_source(tmp_path)
    items = [_item(str(index)) for index in range(meta["observed_count"] if meta else 1)]
    connector = _Connector(FetchResult(items=items, next_cursor="after", acquisition=meta))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)
    task = _task()
    artifacts = fetch_source(tmp_path, source_id=task.target_id, task=task)
    run = RunRepository(tmp_path).get_run(task.task_id)
    assert run["status"] == "success"
    assert len(artifacts["card_build_task_ids"]) == len(items)
    for payload in (artifacts, run["raw_payload"], run["artifacts_summary"]):
        if expected:
            assert payload["completeness_status"] == expected
            assert payload["acquisition"] == meta
            assert payload["observed_count"] == meta["observed_count"]
            assert payload["requested_count"] == 40
            assert payload["stop_reason"] == meta["stop_reason"]
        else:
            assert not {"completeness_status", "acquisition", "requested_count", "observed_count", "stop_reason"} & payload.keys()
    assert run["error_summary"] == ("no_progress" if expected == "partial" else None)
    summary_row: SourceRunSummaryRow = {
        **dict.fromkeys(("platform", "source_type", "display_name", "started_at",
                         "finished_at", "run_id", "commit_status", "commit_started_at", "commit_finished_at",
                         "fetched_count", "visible_count", "new_count", "dedup_skipped_count", "trigger")),
        "task_id": task.task_id, "target_id": task.target_id, "status": "success",
        "execution_status": "success", "trace_available": True,
        "batch_ids": [], "batch_ids_total": 0, "batch_ids_truncated": False,
        "stable_id": task.task_id, "entry_kind": "run", "source_id": task.target_id,
        "task_type": None, "attempt": task.attempt, "execution_event_count": 0,
        "queue_name": task.queue_name, "priority": task.priority, "deadline_at": task.deadline_at,
        "claimed_by": task.claimed_by, "last_failure_class": task.last_failure_class,
        "created_at": "", "scheduled_at": "", "updated_at": None, "next_run_at": task.next_run_at,
        "lease_expires_at": task.lease_expires_at, "cadence_hours": None, "identifier": None,
        "artifacts": parse_run_artifacts({}), "checkpoint": parse_run_checkpoint(artifacts),
        "tracking_acquisition": {},
    }
    projected = project_source_run_summary(summary_row)
    assert projected.get("completeness_status") == expected
    if expected == "partial":
        assert _trace_product_status({**projected, "execution_status": "success"}, {"total": 1, "complete": 1}) == "attention"
