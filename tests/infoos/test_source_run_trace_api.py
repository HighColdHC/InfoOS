from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.handlers.card_finalize import handle_card_finalize
from backend.application.tasks.planner import raw_item_target_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.infrastructure.storage.database import connect
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
    SourceRepository,
)
from backend.read_models.source_run_trace import decode_item_cursor, encode_item_cursor
from backend.read_models.source_run_trace_safety import opaque_identifier
from tests.infoos._factories import make_supported_source_payload


NOW = "2026-07-14T10:00:00Z"


def _ta(value):
    return opaque_identifier("task", value)


def _trace_path(client):
    source_run_id = client.get("/api/source-runs", params={"limit": 100, "offset": 0}).json()["items"][0]["source_run_id"]
    return f"/api/source-runs/{source_run_id}/trace"


def _link(tmp_path, parent_id, *child_ids):
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        row = conn.execute("select artifacts_summary from tasks where task_id=?", (parent_id,)).fetchone()
        artifacts = json.loads(row[0] or "{}")
        artifacts["downstream_task_ids"] = list(child_ids)
        conn.execute("update tasks set artifacts_summary=? where task_id=?", (json.dumps(artifacts), parent_id))


def _seed_run(tmp_path, *, trigger="manual", root="fetch_1", raw_keys=(("connector_a", "raw_1"),)):
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="source_1", connector_id="connector_a", display_name="Source One"
    ))
    repo = TaskRepository.for_base_dir(tmp_path)
    fetch = Task(
        task_id="fetch_1", task_type=TaskType.FETCH, target_kind="source", target_id="source_1",
        status=TaskStatus.SUCCESS, started_at=NOW, finished_at=NOW, result_version="v1",
        artifacts_summary={"chain_id": "chain_1", "chain_root_task_id": root, "trigger": trigger},
    )
    repo.enqueue(fetch)
    build_ids = []
    for index, (connector, raw_id) in enumerate(raw_keys):
        task_id = f"build_{index}"
        build_ids.append(task_id)
        repo.enqueue(Task(
            task_id=task_id, task_type=TaskType.CARD_BUILD, target_kind="raw_item",
            target_id=raw_item_target_id(connector, raw_id), status=TaskStatus.SUCCESS,
            depends_on=(DependencySpec("fetch_1", TaskStatus.SUCCESS),), started_at=NOW,
            finished_at=NOW, result_version="v1",
            artifacts_summary={"chain_id": "chain_1", "chain_root_task_id": root,
                               "card_id": f"card_{index}", "created_or_updated": "created"},
        ))
        CardRepository(tmp_path).upsert_card({
            "card_id": f"card_{index}", "source_id": "source_1", "source_platform": "rss",
            "source_type": "article", "source_url": f"https://cards.test/{index}",
            "title": f"Card {index}", "content": "body", "summary": "summary",
            "captured_at": NOW, "processed_at": NOW,
        })
    checkpoint = {
        "source_id": "source_1", "connector_id": "connector_a", "run_id": "commit_1",
        "raw_item_ids": [raw_id for connector, raw_id in raw_keys if connector == "connector_a"],
        "raw_item_keys": [{"connector_id": connector, "raw_item_id": raw_id} for connector, raw_id in raw_keys],
        "card_build_task_ids": build_ids, "cursor_before": "before", "cursor_after": "after",
        "fetched_count": len(raw_keys), "visible_count": len(raw_keys), "dedup_skipped_count": 2,
    }
    db = resolve_sqlite_path(tmp_path)
    with connect(db) as conn:
        conn.execute(
            """insert into run_history
               (run_id,source_id,connector_id,scheduled_for,started_at,finished_at,status,
                fetched_count,visible_count,new_count,dedup_skipped_count,trigger)
               values (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("commit_1", "source_1", "connector_a", NOW, NOW, NOW, "success",
             len(raw_keys), len(raw_keys), len(raw_keys), 2, trigger),
        )
        conn.execute(
            """insert into acquisition_checkpoints
               (task_id,source_id,run_id,source_input_version,initial_fetch,committed_last_run_at,
                committed_next_run_at,payload,committed_at) values (?,?,?,?,?,?,?,?,?)""",
            ("fetch_1", "source_1", "commit_1", "v1", int(trigger == "initial"), NOW, NOW,
             json.dumps(checkpoint | {"initial_fetch": trigger == "initial"}), NOW),
        )
        for connector, raw_id in raw_keys:
            conn.execute(
                """insert into raw_items
                   (connector_id,raw_item_id,source_id,run_id,source_platform,source_type,source_url,
                    captured_at,raw_payload,first_seen_at,last_seen_at)
                   values (?,?,?,?,?,?,?,?,?,?,?)""",
                (connector, raw_id, "source_1", "later_run", "rss", "article",
                 f"https://current.test/{connector}/{raw_id}", NOW, "{}", NOW, NOW),
            )
    return TestClient(create_app(tmp_path))


def _seed_retired_step(tmp_path, task_id, task_type, status=TaskStatus.FAILED):
    task = Task(
        task_id, task_type, "card", "card_0", status,
        depends_on=(DependencySpec("build_0", TaskStatus.SUCCESS),),
        artifacts_summary={"chain_id": "chain_1", "card_id": "card_0",
                           "provider_response_body": "raw provider response",
                           "api_key": "secret-trace-key"},
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(task)
    return task


def _seed_xhs_acceptance_run(tmp_path):
    raw_keys = tuple(("xhs_connector", f"note_{index}") for index in range(7))
    client = _seed_run(tmp_path, raw_keys=raw_keys)
    repo = TaskRepository.for_base_dir(tmp_path)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            "update sources set platform='xiaohongshu', source_type='creator', "
            "display_name='小红书测试账号', connector_id='xhs_connector' where source_id='source_1'"
        )
        checkpoint = json.loads(conn.execute(
            "select payload from acquisition_checkpoints where task_id='fetch_1'"
        ).fetchone()[0])
        checkpoint.update({
            "connector_id": "xhs_connector",
            "discovered_link_count": 10,
            "dedup_skipped_count": 3,
            "raw_item_count": 7,
            "discovered_links": [f"https://www.xiaohongshu.com/explore/link-{index}" for index in range(10)],
            "discovery_availability": "available",
            "discovery_evidence": {
                "executor": "opencli",
                "session_alias": "xiaohongshu-main",
                "sanitized_arguments": ["xiaohongshu", "user-notes", "[REDACTED]"],
                "availability": "available",
            },
        })
        conn.execute(
            "update acquisition_checkpoints set payload=? where task_id='fetch_1'",
            (json.dumps(checkpoint),),
        )
        conn.execute(
            "update run_history set visible_count=10, fetched_count=7, new_count=7, "
            "dedup_skipped_count=3 where run_id='commit_1'"
        )
        conn.execute("update tasks set attempt=2 where task_id='build_0'")
    TaskEventRepository(tmp_path).append(
        task_id="build_0",
        chain_id="chain_1",
        event_type="retry_scheduled",
        status="pending",
        payload={"attempt": 2},
        occurred_at="2026-07-14T09:59:00Z",
    )

    for index in range(7):
        finalize_id = f"finalize_{index}"
        completeness = "partial" if index == 0 else "complete"
        if index < 2:
            media_id = f"media_{index}"
            assets = []
            image_total = 7 if index == 0 else 3
            video_total = 2 if index == 0 else 1
            for asset_index in range(image_total):
                asset_id = f"image_{index}_{asset_index}"
                status = "downloaded"
                CardAssetRepository(tmp_path).upsert_asset({
                    "asset_id": asset_id, "card_id": f"card_{index}", "asset_kind": "image",
                    "source_url": f"https://img.test/{asset_id}", "status": status,
                })
                assets.append({"asset_id": asset_id, "asset_kind": "image", "status": status})
            video_ids = []
            for asset_index in range(video_total):
                asset_id = f"video_{index}_{asset_index}"
                status = "failed" if index == 0 and asset_index == 1 else "downloaded"
                CardAssetRepository(tmp_path).upsert_asset({
                    "asset_id": asset_id, "card_id": f"card_{index}", "asset_kind": "video",
                    "source_url": f"https://video.test/{asset_id}", "status": status,
                })
                assets.append({"asset_id": asset_id, "asset_kind": "video", "status": status})
                video_ids.append(asset_id)
            repo.enqueue(Task(
                media_id, TaskType.MEDIA_DOWNLOAD, "card", f"card_{index}",
                TaskStatus.FAILED if index == 0 else TaskStatus.SUCCESS,
                (DependencySpec(f"build_{index}", TaskStatus.SUCCESS),),
                attempt=3 if index == 0 else 1, max_attempts=4,
                artifacts_summary={"chain_id": "chain_1", "card_id": f"card_{index}", "assets": assets},
                started_at=NOW, finished_at=NOW,
            ))
            transcript_ids = []
            for asset_index, asset_id in enumerate(video_ids):
                transcript_id = f"transcript_{index}_{asset_index}"
                transcript_ids.append(transcript_id)
                repo.enqueue(Task(
                    transcript_id, TaskType.VIDEO_TRANSCRIBE, "asset", asset_id, TaskStatus.SUCCESS,
                    (DependencySpec(media_id, TaskStatus.SUCCESS),),
                    attempt=2 if index == 0 and asset_index == 0 else 1,
                    artifacts_summary={
                        "chain_id": "chain_1", "card_id": f"card_{index}",
                        "asset_id": asset_id, "asset_kind": "video", "total": 1,
                        "success": 1, "failed": 0,
                    },
                    started_at=NOW, finished_at=NOW,
                ))
                CardContentBlockRepository(tmp_path).upsert_block({
                    "block_id": f"transcript_block_{index}_{asset_index}",
                    "card_id": f"card_{index}", "position": 50 + asset_index,
                    "layer": "derived", "block_kind": "video_transcript",
                    "status": "enriched", "body": f"transcript {asset_id}",
                    "raw_payload": {"asset_id": asset_id},
                })
            _link(tmp_path, f"build_{index}", media_id)
            _link(tmp_path, media_id, *transcript_ids)
            for transcript_id in transcript_ids:
                _link(tmp_path, transcript_id, finalize_id)
            dependencies = tuple(DependencySpec(task_id, TaskStatus.SUCCESS) for task_id in transcript_ids)
        else:
            dependencies = (DependencySpec(f"build_{index}", TaskStatus.SUCCESS),)
            _link(tmp_path, f"build_{index}", finalize_id)
        repo.enqueue(Task(
            finalize_id, TaskType.CARD_FINALIZE, "card", f"card_{index}", TaskStatus.SUCCESS,
            dependencies,
            artifacts_summary={
                "chain_id": "chain_1", "card_id": f"card_{index}",
                "completeness_status": completeness, "card_version": 1,
            },
            started_at=NOW, finished_at=NOW,
        ))
    return client


def test_xhs_trace_projects_persisted_discovery_card_and_item_media_evidence(tmp_path):
    client = _seed_xhs_acceptance_run(tmp_path)

    response = client.get(_trace_path(client), params={"item_limit": 20})

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["source_run_id"] == _ta("fetch_1")
    assert payload["run"]["platform"] == "xiaohongshu"
    assert payload["run"]["fetch_stats"] == {
        "total_links": 10, "dedup_skipped": 3, "pending_items": 7, "raw_item_count": 7,
    }
    assert payload["run"]["card_progress"] == {
        "total": 7, "complete": 6, "partial": 1, "failed": 0,
    }
    assert payload["discovery"]["availability"] == "available"
    assert payload["discovery"]["total_links"] == 10
    assert payload["discovery"]["dedup_skipped"] == 3
    assert payload["discovery"]["pending_items"] == 7
    assert payload["discovery"]["evidence"] == {
        "executor": "opencli",
        "session_alias": "xiaohongshu-main",
        "sanitized_arguments": ["xiaohongshu", "user-notes", "[REDACTED]"],
        "fetch_task_id": _ta("fetch_1"),
        "links": [f"https://www.xiaohongshu.com/explore/link-{index}" for index in range(10)],
        "availability": "available",
    }
    assert payload["card_build"] == {
        "total": 7, "complete": 6, "partial": 1, "failed": 0, "retry_count": 4,
    }
    assert len(payload["items"]) == 7
    first = payload["items"][0]
    count_fields = ("total", "success", "failed", "retry_count")
    assert {key: first["image_download"][key] for key in count_fields} == {
        "total": 7, "success": 7, "failed": 0, "retry_count": 2,
    }
    assert {key: first["video_download"][key] for key in count_fields} == {
        "total": 2, "success": 1, "failed": 1, "retry_count": 2,
    }
    assert {key: first["video_transcription"][key] for key in count_fields} == {
        "total": 2, "success": 2, "failed": 0, "retry_count": 1,
    }
    assert first["video_download"]["tasks"] == [{
        "task_id": _ta("media_0"), "status": "failed",
        "error_summary": None, "retryable": True,
    }]
    assert first["finalize"]["completeness"] == "partial"
    assert first["finalize"]["status"] == "success"
    assert first["finalize"]["version"] == 1


def test_trace_card_progress_covers_every_reused_item_beyond_list_graph_limit(tmp_path):
    raw_keys = tuple(("connector_a", f"raw_{index}") for index in range(80))
    client = _seed_run(tmp_path, raw_keys=raw_keys)
    repo = TaskRepository.for_base_dir(tmp_path)
    downstream_by_build = {}
    for index in range(80):
        finalize_id = f"finalize_{index}"
        completeness = "complete" if index < 68 else "partial"
        repo.enqueue(Task(
            finalize_id,
            TaskType.CARD_FINALIZE,
            "card",
            f"card_{index}",
            status=TaskStatus.SUCCESS,
            depends_on=(DependencySpec(f"build_{index}", TaskStatus.SUCCESS),),
            artifacts_summary={
                "chain_id": "chain_1" if index < 5 else f"reused_chain_{index}",
                "card_id": f"card_{index}",
                "version": 1,
                "completeness_status": completeness,
            },
            started_at=NOW,
            finished_at=NOW,
        ))
        downstream_by_build[f"build_{index}"] = finalize_id
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        for build_id, finalize_id in downstream_by_build.items():
            artifacts = json.loads(conn.execute(
                "select artifacts_summary from tasks where task_id=?",
                (build_id,),
            ).fetchone()[0])
            artifacts["downstream_task_ids"] = [finalize_id]
            conn.execute(
                "update tasks set artifacts_summary=? where task_id=?",
                (json.dumps(artifacts), build_id),
            )

    payload = client.get(_trace_path(client)).json()

    assert len(payload["items"]) == 25
    assert payload["item_page"]["item_total"] == 80
    assert payload["run"]["card_progress"] == {
        "total": 80,
        "complete": 68,
        "partial": 12,
        "failed": 0,
    }


def test_trace_projects_version_from_card_finalize_handler_artifacts(tmp_path):
    client = _seed_run(tmp_path)
    finalize = Task(
        "finalize_handler_contract",
        TaskType.CARD_FINALIZE,
        "card",
        "card_0",
        artifacts_summary={"chain_id": "chain_1", "chain_root_task_id": "fetch_1"},
    )
    result = handle_card_finalize(finalize, tmp_path)
    assert result["status"] == "success"
    assert result["artifacts"]["version"] == 1

    TaskRepository.for_base_dir(tmp_path).enqueue(Task(
        "finalize_handler_contract",
        TaskType.CARD_FINALIZE,
        "card",
        "card_0",
        status=TaskStatus.SUCCESS,
        depends_on=(DependencySpec("build_0", TaskStatus.SUCCESS),),
        artifacts_summary=finalize.artifacts_summary | {"card_version": 99} | result["artifacts"],
        started_at=NOW,
        finished_at=NOW,
    ))
    _link(tmp_path, "build_0", "finalize_handler_contract")

    item = client.get(_trace_path(client)).json()["item_page"]["items"][0]

    assert item["finalize"]["version"] == 1


def test_trace_recovers_only_explicitly_referenced_cross_chain_finalize(tmp_path):
    client = _seed_run(tmp_path, trigger="initial")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "finalize_shared_foreign_chain",
        TaskType.CARD_FINALIZE,
        "card",
        "card_0",
        status=TaskStatus.SUCCESS,
        artifacts_summary={
            "chain_id": "chain_from_other_source",
            "chain_root_task_id": "fetch_from_other_source",
            "card_id": "card_0",
            "version": 1,
            "content_hash": "sha256:shared",
            "completeness_status": "partial",
        },
        started_at=NOW,
        finished_at=NOW,
    ))
    _link(tmp_path, "build_0", "finalize_shared_foreign_chain")

    list_item = client.get("/api/source-runs").json()["items"][0]
    trace = client.get(_trace_path(client)).json()
    finalize_stage = next(
        stage for stage in trace["items"][0]["stages"]
        if stage["stage"] == "card_finalize"
    )

    assert list_item["card_progress"] == {
        "total": 1,
        "complete": 0,
        "partial": 1,
        "failed": 0,
    }
    assert trace["run"]["execution_status"] == "success"
    assert trace["card_build"] == {
        "total": 1,
        "complete": 0,
        "partial": 1,
        "failed": 0,
        "retry_count": 0,
    }
    assert trace["items"][0]["finalize"] == {
        "status": "success",
        "version": 1,
        "completeness": "partial",
        "task_id": _ta("finalize_shared_foreign_chain"),
        "availability": "available",
    }
    assert finalize_stage["association"] == "referenced_existing_pipeline"
    assert finalize_stage["execution"] == "not_applicable"


def test_trace_does_not_recover_cross_chain_finalize_without_explicit_reference(tmp_path):
    client = _seed_run(tmp_path, trigger="initial")
    TaskRepository.for_base_dir(tmp_path).enqueue(Task(
        "finalize_unreferenced_foreign_chain",
        TaskType.CARD_FINALIZE,
        "card",
        "card_0",
        status=TaskStatus.SUCCESS,
        artifacts_summary={
            "chain_id": "chain_from_other_source",
            "chain_root_task_id": "fetch_from_other_source",
            "card_id": "card_0",
            "version": 1,
            "content_hash": "sha256:shared",
            "completeness_status": "partial",
        },
        started_at=NOW,
        finished_at=NOW,
    ))

    list_item = client.get("/api/source-runs").json()["items"][0]
    trace = client.get(_trace_path(client)).json()

    assert list_item["card_progress"] == {
        "total": 0,
        "complete": 0,
        "partial": 0,
        "failed": 0,
    }
    assert trace["card_build"] == {
        "total": 1,
        "complete": None,
        "partial": None,
        "failed": None,
        "retry_count": 0,
        "availability": "unavailable",
    }
    assert trace["items"][0]["finalize"] == {
        "status": "unavailable",
        "version": None,
        "completeness": "unavailable",
        "task_id": "unavailable",
        "availability": "unavailable",
    }


@pytest.mark.parametrize(
    ("task_type", "target_kind", "target_id"),
    [
        (TaskType.CONTENT_ENRICH, "card", "card_0"),
        (TaskType.CARD_FINALIZE, "asset", "card_0"),
        (TaskType.CARD_FINALIZE, "card", "card_other"),
    ],
)
def test_trace_rejects_explicit_cross_chain_reference_with_wrong_type_or_target(
    tmp_path, task_type, target_kind, target_id
):
    client = _seed_run(tmp_path, trigger="initial")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "wrong_cross_chain_target",
        task_type,
        target_kind,
        target_id,
        status=TaskStatus.SUCCESS,
        artifacts_summary={
            "chain_id": "chain_from_other_source",
            "chain_root_task_id": "fetch_from_other_source",
            "card_id": target_id,
            "version": 1,
            "completeness_status": "partial",
        },
        started_at=NOW,
        finished_at=NOW,
    ))
    _link(tmp_path, "build_0", "wrong_cross_chain_target")

    trace = client.get(_trace_path(client)).json()

    assert trace["card_build"]["availability"] == "unavailable"
    assert trace["items"][0]["finalize"]["availability"] == "unavailable"
    assert all(stage["task_id"] != _ta("wrong_cross_chain_target") for stage in trace["items"][0]["stages"])


def test_old_trace_reused_finalize_is_stable_after_current_card_state_changes(tmp_path):
    client = _seed_run(tmp_path, trigger="initial")
    TaskRepository.for_base_dir(tmp_path).enqueue(Task(
        "finalize_stable_foreign_chain",
        TaskType.CARD_FINALIZE,
        "card",
        "card_0",
        status=TaskStatus.SUCCESS,
        artifacts_summary={
            "chain_id": "chain_from_other_source",
            "chain_root_task_id": "fetch_from_other_source",
            "card_id": "card_0",
            "version": 1,
            "content_hash": "sha256:original",
            "completeness_status": "partial",
        },
        started_at=NOW,
        finished_at=NOW,
    ))
    _link(tmp_path, "build_0", "finalize_stable_foreign_chain")
    before_list = client.get("/api/source-runs").json()["items"][0]["card_progress"]
    before_trace = client.get(_trace_path(client)).json()

    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            """update cards
               set status='active', version=99, content_hash='sha256:rewritten',
                   completeness_status='complete', source_run_id='later_fetch', updated_at=?
               where card_id='card_0'""",
            ("2026-07-17T10:00:00Z",),
        )

    after_list = client.get("/api/source-runs").json()["items"][0]["card_progress"]
    after_trace = client.get(_trace_path(client)).json()

    assert after_list == before_list
    assert after_trace["card_build"] == before_trace["card_build"]
    assert after_trace["items"][0]["finalize"] == before_trace["items"][0]["finalize"]
    before_finalize_stage = next(
        stage for stage in before_trace["items"][0]["stages"] if stage["stage"] == "card_finalize"
    )
    after_finalize_stage = next(
        stage for stage in after_trace["items"][0]["stages"] if stage["stage"] == "card_finalize"
    )
    assert after_finalize_stage == before_finalize_stage


def test_trace_reports_unavailable_instead_of_zero_when_discovery_or_finalize_evidence_is_missing(tmp_path):
    client = _seed_run(tmp_path)

    payload = client.get(_trace_path(client)).json()

    assert payload["discovery"] == {
        "total_links": None,
        "dedup_skipped": None,
        "pending_items": None,
        "availability": "unavailable",
        "evidence": {
            "executor": "unavailable",
            "session_alias": "unavailable",
            "sanitized_arguments": [],
            "fetch_task_id": _ta("fetch_1"),
            "links": [],
            "availability": "unavailable",
        },
    }
    assert payload["card_build"] == {
        "total": 1,
        "complete": None,
        "partial": None,
        "failed": None,
        "retry_count": 0,
        "availability": "unavailable",
    }
    assert "discovery evidence unavailable" in payload["gaps"]
    assert "card finalize evidence incomplete" in payload["gaps"]


@pytest.mark.parametrize("trigger", ["manual", "initial", "scheduled", "calibration", "recovery", "precommit"])
def test_fetch_based_list_identity_trigger_and_calibration_root(tmp_path, trigger):
    client = _seed_run(tmp_path, trigger=trigger, root="calibration_1" if trigger == "calibration" else "fetch_1")
    payload = client.get("/api/source-runs", params={"limit": 1, "offset": 0}).json()
    assert payload["total"] == 1
    assert payload["items"][0]["source_run_id"] == _ta("fetch_1")
    assert payload["items"][0]["chain_root"] == _ta("calibration_1" if trigger == "calibration" else "fetch_1")
    assert payload["items"][0]["trigger"] == trigger
    assert payload["items"][0]["aggregate"] == {"fetched": 1, "visible": 1, "new": 1, "dedup_skipped": 2}


def test_fetch_list_has_stable_offset_pages_and_strict_bounds(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("fetch_2", TaskType.FETCH, "source", "source_1"))
    repo.enqueue(Task("fetch_3", TaskType.FETCH, "source", "source_1", attempt=2))
    first = client.get("/api/source-runs", params={"limit": 2, "offset": 0}).json()
    second = client.get("/api/source-runs", params={"limit": 2, "offset": 2}).json()
    ids = [row["fetch_task_id"] for row in first["items"] + second["items"]]
    assert ids == [_ta("fetch_2"), _ta("fetch_3"), _ta("fetch_1")]
    assert first["total"] == 3 and first["next_offset"] == 2
    assert second["next_offset"] is None
    executions = {row["fetch_task_id"]: row["acquisition_stage"]["command_executions"]
                  for row in first["items"] + second["items"]}
    assert executions[_ta("fetch_2")] == 0
    assert executions[_ta("fetch_3")] == 1
    assert client.get("/api/source-runs", params={"limit": 0}).status_code == 422
    assert client.get(_trace_path(client), params={"item_limit": 101}).status_code == 422


def test_list_counts_durable_fetch_executions_without_retry_inflation(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "fetch_retrying", TaskType.FETCH, "source", "source_1",
        status=TaskStatus.PENDING, attempt=3,
    ))
    repo.enqueue(Task(
        "fetch_terminal", TaskType.FETCH, "source", "source_1",
        status=TaskStatus.FAILED, attempt=4, last_failure_class="opencli_command_failed",
    ))
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        for event_id, task_id, event_type in (
            ("event_retry_1", "fetch_retrying", "retry_scheduled"),
            ("event_retry_2", "fetch_retrying", "retry_scheduled"),
            ("event_terminal_retry_1", "fetch_terminal", "retry_scheduled"),
            ("event_terminal_retry_2", "fetch_terminal", "retry_scheduled"),
            ("event_terminal_failed", "fetch_terminal", "failed"),
        ):
            conn.execute(
                """insert into task_events
                   (event_id, task_id, event_type, status, payload, occurred_at)
                   values (?, ?, ?, ?, '{}', ?)""",
                (event_id, task_id, event_type, "pending", NOW),
            )

    items = client.get("/api/source-runs", params={"limit": 100}).json()["items"]
    executions = {
        row["fetch_task_id"]: row["acquisition_stage"]["command_executions"]
        for row in items
    }
    assert executions[_ta("fetch_retrying")] == 2
    assert executions[_ta("fetch_terminal")] == 3


def test_list_source_run_alias_is_the_public_trace_identity(tmp_path):
    client = _seed_run(tmp_path)
    source_run_id = client.get("/api/source-runs").json()["items"][0]["source_run_id"]
    response = client.get(f"/api/source-runs/{source_run_id}/trace")
    assert response.status_code == 200
    assert response.json()["source_run_id"] == source_run_id
    assert response.json()["run"]["fetch_task_id"] == source_run_id
    assert client.get("/api/source-runs/fetch_1/trace").status_code == 400
    assert client.get("/api/source-runs/task_000000000000000000000000/trace").status_code == 404


def test_trace_marks_runs_older_than_event_hot_window(tmp_path):
    client = _seed_run(tmp_path)
    path = _trace_path(client)
    old = client.get(path).json()
    assert old["events_archived"] is True
    assert old["events_hot_window_days"] == 30

    recent = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("update run_history set scheduled_for=?", (recent,))
    assert client.get(path).json()["events_archived"] is False


def test_trace_run_projects_registry_owned_source_type_and_task_view(tmp_path):
    client = _seed_run(tmp_path)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            "update sources set platform='zsxq', source_type='keyword' "
            "where source_id='source_1'"
        )

    trace = client.get(_trace_path(client)).json()

    assert trace["run"]["source_type"] == "keyword"
    assert trace["run"]["task_view"] == "research"
    assert "research_summary" not in trace
    assert "research_cards" not in trace
    assert "discovery" in trace
    assert "card_progress" in trace["run"]
    assert "item_page" in trace


def test_list_merges_planned_boundaries_and_dedupes_exact_scheduled_occurrence(tmp_path):
    _seed_run(tmp_path)
    from backend.read_models.source_run_trace import read_source_runs

    now = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
    horizon = now + timedelta(hours=24)
    source_repo = SourceRepository(tmp_path)
    source_repo.upsert_source(make_supported_source_payload(
        source_id="source_1", connector_id="connector_a", display_name="Source One",
        next_run_at=horizon.isoformat().replace("+00:00", "Z"),
    ))
    source_repo.upsert_source(make_supported_source_payload(
        source_id="source_now", connector_id="connector_now", display_name="Source Now",
        next_run_at=NOW,
    ))
    source_repo.upsert_source(make_supported_source_payload(
        source_id="source_far", connector_id="connector_far", display_name="Source Far",
        next_run_at=(horizon + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
    ))
    TaskRepository.for_base_dir(tmp_path).enqueue(Task(
        "fetch_far", TaskType.FETCH, "source", "source_far",
        next_run_at=(horizon + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
    ))

    first = read_source_runs(tmp_path, limit=100, offset=0, evaluated_at=NOW)
    planned_sources = {row["source_id"] for row in first["items"] if row["entry_kind"] == "planned"}
    assert planned_sources == {"source_1", "source_now"}
    assert first["planned_horizon_end"] == "2026-07-15T10:00:00Z"
    assert all(row["fetch_task_id"] != _ta("fetch_far") for row in first["items"])

    TaskRepository.for_base_dir(tmp_path).enqueue(Task(
        "fetch_horizon", TaskType.FETCH, "source", "source_1",
        next_run_at=(horizon + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        artifacts_summary={"scheduled_for": horizon.isoformat().replace("+00:00", "Z")},
    ))
    second = read_source_runs(tmp_path, limit=100, offset=0, evaluated_at=NOW)
    source_one = [row for row in second["items"] if row["source_id"] == "source_1"]
    assert [row["entry_kind"] for row in source_one].count("planned") == 0
    assert [row["entry_kind"] for row in source_one].count("run") == 2


def test_list_time_and_normalized_status_filters_are_server_side(tmp_path):
    client = _seed_run(tmp_path)
    TaskRepository.for_base_dir(tmp_path).enqueue(Task(
        "fetch_blocked", TaskType.FETCH, "source", "source_1", TaskStatus.BLOCKED,
        next_run_at="2026-07-14T11:00:00Z",
    ))

    inclusive = client.get(
        "/api/source-runs", params={"time_from": NOW, "time_to": NOW, "status": "attention"}
    )
    assert inclusive.status_code == 200
    assert inclusive.json()["total"] == 1
    assert inclusive.json()["items"][0]["scheduled_at"] == NOW

    assert client.get("/api/source-runs", params={"status": "failed"}).status_code == 422
    reversed_range = client.get(
        "/api/source-runs", params={"time_from": "2026-07-15T00:00:00Z", "time_to": NOW}
    )
    assert reversed_range.status_code == 422
    assert client.get("/api/source-runs", params={"status": "blocked"}).status_code == 422


def test_list_paginates_merged_rows_and_reports_honest_stage_metrics(tmp_path):
    client = _seed_run(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("update sources set next_run_at=? where source_id='source_1'", (future,))
    repo = TaskRepository.for_base_dir(tmp_path)
    chain = {"chain_id": "chain_1", "chain_root_task_id": "fetch_1"}
    tasks = [
        Task("enrich_pending", TaskType.CONTENT_ENRICH, "card", "card_0", TaskStatus.PENDING,
             (DependencySpec("build_0", TaskStatus.SUCCESS),), artifacts_summary=chain),
        Task("media_done", TaskType.MEDIA_DOWNLOAD, "card", "card_0", TaskStatus.SUCCESS,
             (DependencySpec("enrich_pending", TaskStatus.PENDING),),
             artifacts_summary=chain | {"assets": [
                 {"asset_id": "image_metric", "asset_kind": "image"},
                 {"asset_id": "video_metric", "asset_kind": "video"},
             ]}),
        Task("transcribe_skipped", TaskType.VIDEO_TRANSCRIBE, "asset", "video_metric", TaskStatus.SKIPPED,
             (DependencySpec("media_done", TaskStatus.SUCCESS),), artifacts_summary=chain),
        Task("finalize_done", TaskType.CARD_FINALIZE, "card", "card_0", TaskStatus.SUCCESS,
             artifacts_summary=chain | {"upstream_media_tasks": [
                 {"task_id": "transcribe_skipped", "status": "skipped"}
             ]}),
        Task("context_done", TaskType.CONTEXT_PACK, "card", "card_0", TaskStatus.SUCCESS,
             (DependencySpec("transcribe_skipped", TaskStatus.SKIPPED),), artifacts_summary=chain),
        Task("summary_failed", TaskType.SUMMARY, "card", "card_0", TaskStatus.FAILED,
             (DependencySpec("context_done", TaskStatus.SUCCESS),), artifacts_summary=chain),
    ]
    for task in tasks:
        repo.enqueue(task)
    _link(tmp_path, "build_0", "enrich_pending")
    _link(tmp_path, "enrich_pending", "media_done")
    _link(tmp_path, "media_done", "transcribe_skipped")
    _link(tmp_path, "transcribe_skipped", "finalize_done")
    _link(tmp_path, "transcribe_skipped", "finalize_done", "context_done")
    _link(tmp_path, "context_done", "summary_failed")
    CardAssetRepository(tmp_path).upsert_asset({
        "asset_id": "image_metric", "card_id": "card_0", "asset_kind": "image",
        "source_url": "https://assets.test/image", "status": "downloaded",
    })
    CardAssetRepository(tmp_path).upsert_asset({
        "asset_id": "video_metric", "card_id": "card_0", "asset_kind": "video",
        "source_url": "https://assets.test/video", "status": "failed",
    })

    first = client.get("/api/source-runs", params={"limit": 1, "offset": 0}).json()
    second = client.get("/api/source-runs", params={"limit": 1, "offset": 1}).json()
    assert first["total"] == second["total"] == 2
    assert first["next_offset"] == 1 and second["next_offset"] is None
    exhausted = client.get("/api/source-runs", params={"limit": 1, "offset": 99}).json()
    assert exhausted["total"] == 2 and exhausted["items"] == [] and exhausted["next_offset"] is None
    assert first["items"][0]["entry_kind"] == "planned"
    assert first["items"][0]["trace_available"] is False
    assert first["items"][0]["acquisition_stage"]["command_executions"] == 0
    run = second["items"][0]
    assert run["entry_kind"] == "run" and run["trace_available"] is True
    assert run["acquisition_stage"] == {
        "command_executions": 1, "command_successes": 1, "pending_enrichment_links": 1,
    }
    assert run["enrichment_stage"] == {
        "image_download": {"total": 1, "success": 1, "failed": 0},
        "video_download": {"total": 1, "success": 0, "failed": 1},
        "video_transcription": {"total": 1, "success": 0, "failed": 1},
        "card_pack": {"total": 1, "success": 1, "failed": 0},
    }
    assert "ai_summary" not in run


def test_trace_uses_composite_keys_current_url_and_pending_execution(tmp_path):
    client = _seed_run(tmp_path, raw_keys=(("connector_a", "same"), ("connector_b", "same")))
    repo = TaskRepository.for_base_dir(tmp_path)
    pending = Task(
        task_id="pending_enrich", task_type=TaskType.CONTENT_ENRICH, target_kind="card", target_id="card_0",
        depends_on=(DependencySpec("build_0", TaskStatus.SUCCESS),),
        artifacts_summary={"chain_id": "chain_1", "chain_root_task_id": "fetch_1"},
    )
    repo.enqueue(pending)
    _link(tmp_path, "build_0", "pending_enrich")
    payload = client.get(_trace_path(client)).json()
    items = payload["item_page"]["items"]
    assert [(item["connector_id"], item["raw_item_id"]) for item in items] == [
        ("connector_a", "same"), ("connector_b", "same")
    ]
    assert items[0]["current_projection_url"] == "https://current.test/connector_a/same"
    assert "committed_url" not in json.dumps(payload).lower()
    pending_stage = next(stage for stage in items[0]["stages"] if stage["task_id"] == _ta("pending_enrich"))
    assert pending_stage["association"] == "associated_with_this_run"
    assert pending_stage["execution"] == "not_started"


def test_trace_projects_persisted_content_enrich_evidence_per_item(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "enrich_failed", TaskType.CONTENT_ENRICH, "card", "card_0",
        status=TaskStatus.FAILED,
        depends_on=(DependencySpec("build_0", TaskStatus.SUCCESS),),
        attempt=2,
        max_attempts=4,
        last_failure_class="transient_network",
        artifacts_summary={"chain_id": "chain_1", "chain_root_task_id": "fetch_1"},
    ))
    _link(tmp_path, "build_0", "enrich_failed")
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            "update tasks set error_reason=? where task_id='enrich_failed'",
            ("token=super-secret provider temporarily unavailable",),
        )

    item = client.get(_trace_path(client)).json()["item_page"]["items"][0]

    assert item["content_enrich"] == {
        "availability": "available",
        "task_id": _ta("enrich_failed"),
        "status": "failed",
        "attempt": 2,
        "max_attempts": 4,
        "retryable": True,
        "error": {
            "class": "transient_network",
            "summary": "token=[REDACTED] provider temporarily unavailable",
            "next_action": "retry",
        },
    }
    assert "super-secret" not in json.dumps(item)


def test_trace_marks_content_enrich_unavailable_without_persisted_task(tmp_path):
    client = _seed_run(tmp_path)

    item = client.get(_trace_path(client)).json()["item_page"]["items"][0]

    assert item["content_enrich"] == {
        "availability": "unavailable",
        "task_id": "unavailable",
        "status": "unavailable",
        "attempt": None,
        "max_attempts": None,
        "retryable": False,
        "error": None,
    }
    assert "card_llm_enrich" not in item
    assert client.get(_trace_path(client)).json()["retired_steps"] == {
        "count": 0, "availability": "available",
    }


def test_trace_groups_all_retired_types_without_task_or_recovery_details(tmp_path):
    from backend.domain.tasks import RETIRED_TASK_TYPES

    client = _seed_run(tmp_path)
    tasks = [_seed_retired_step(tmp_path, f"retired_{kind.value}", kind)
             for kind in RETIRED_TASK_TYPES]
    _link(tmp_path, "build_0", *(task.task_id for task in tasks))
    payload = client.get(_trace_path(client)).json()
    assert payload["retired_steps"] == {"count": len(tasks), "availability": "available"}
    assert {row["task_type"] for row in payload["task_tracking"]}.isdisjoint(RETIRED_TASK_TYPES)
    item = payload["items"][0]
    assert "card_llm_enrich" not in item
    assert {stage["stage"] for stage in item["stages"]}.isdisjoint(RETIRED_TASK_TYPES)
    assert "secret-trace-key" not in json.dumps(payload)
    assert "raw provider response" not in json.dumps(payload)
    assert all(TaskRepository.for_base_dir(tmp_path).get(task.task_id) for task in tasks)


def test_trace_retired_count_is_deduplicated_and_independent_of_item_page(tmp_path):
    client = _seed_run(tmp_path, raw_keys=(("connector_a", "raw_0"), ("connector_a", "raw_1")))
    task = _seed_retired_step(tmp_path, "retired_shared", TaskType.IMAGE_UNDERSTAND)
    _link(tmp_path, "build_0", task.task_id)
    _link(tmp_path, "build_1", task.task_id)
    first = client.get(_trace_path(client), params={"item_limit": 1}).json()
    second = client.get(_trace_path(client), params={"item_limit": 1, "cursor": first["item_page"]["next_cursor"]}).json()
    assert first["retired_steps"] == second["retired_steps"] == {"count": 1, "availability": "available"}


def test_trace_retired_count_marks_truncated_graph_partial(tmp_path, monkeypatch):
    from backend.infrastructure.storage.source_run_trace_queries import SourceRunTraceQueries

    client = _seed_run(tmp_path)
    original = SourceRunTraceQueries.load_list_pipeline_rows

    def truncated(self, rows):
        loaded = original(self, rows)
        loaded["page_truncated"] = True
        return loaded

    monkeypatch.setattr(SourceRunTraceQueries, "load_list_pipeline_rows", truncated)
    assert client.get(_trace_path(client)).json()["retired_steps"] == {
        "count": 0, "availability": "partial",
    }


def test_reuse_and_cross_chain_dependency_are_not_attributed(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    external = Task(
        task_id="external_enrich", task_type=TaskType.CONTENT_ENRICH, target_kind="card", target_id="card_0",
        status=TaskStatus.SUCCESS, result_version="v1", started_at=NOW, finished_at=NOW,
        artifacts_summary={"chain_id": "old_chain", "chain_root_task_id": "old_fetch"},
    )
    repo.enqueue(external)
    finalize = Task(
        task_id="finalize_1", task_type=TaskType.CARD_FINALIZE, target_kind="card", target_id="card_0",
        depends_on=(DependencySpec("build_0", TaskStatus.SUCCESS), DependencySpec("external_enrich", TaskStatus.SUCCESS)),
        artifacts_summary={"chain_id": "chain_1", "chain_root_task_id": "fetch_1"},
    )
    repo.enqueue(finalize)
    _link(tmp_path, "build_0", "finalize_1")
    stage = next(stage for stage in client.get(_trace_path(client)).json()["item_page"]["items"][0]["stages"] if stage["task_id"] == _ta("finalize_1"))
    external_dep = next(dep for dep in stage["dependencies"] if dep["task_id"] == _ta("external_enrich"))
    assert external_dep["association"] == "external_dependency"

    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("update tasks set depends_on='[]', artifacts_summary=? where task_id='build_0'",
                     (json.dumps({"chain_id": "old_chain", "card_id": "card_0"}),))
    item = client.get(_trace_path(client)).json()["item_page"]["items"][0]
    assert item["association"] == "referenced_existing_pipeline"
    assert item["execution"] == "not_applicable"
    assert item["card_projection_provenance"] == "reused_existing_pipeline"


def test_cursor_is_canonical_and_pages_without_duplicates(tmp_path):
    client = _seed_run(tmp_path, raw_keys=(("connector_a", "a"), ("connector_a", "b"), ("connector_b", "a")))
    first = client.get(_trace_path(client), params={"item_limit": 2}).json()["item_page"]
    assert first["truncated"] is True
    assert decode_item_cursor(first["next_cursor"]) == 2
    second = client.get(_trace_path(client), params={"item_limit": 2, "cursor": first["next_cursor"]}).json()["item_page"]
    assert [(row["connector_id"], row["raw_item_id"]) for row in second["items"]] == [("connector_b", "a")]
    assert second["next_cursor"] is None
    assert encode_item_cursor(2) == encode_item_cursor(2).rstrip("=")
    assert client.get(_trace_path(client), params={"cursor": "not-json"}).status_code == 400


def test_current_card_media_order_asset_safety_and_error_redaction(tmp_path):
    client = _seed_run(tmp_path)
    block_repo = CardContentBlockRepository(tmp_path)
    asset_repo = CardAssetRepository(tmp_path)
    for asset_id, local_path, created in (
        ("asset_late", "card/image.jpg", "2026-01-01T00:00:02Z"),
        ("asset_early", "../secret", "2026-01-01T00:00:01Z"),
        ("asset_abs", "/tmp/secret", "2026-01-01T00:00:03Z"),
        ("asset_remote", None, "2026-01-01T00:00:04Z"),
    ):
        asset_repo.upsert_asset({"asset_id": asset_id, "card_id": "card_0", "asset_kind": "image",
                                 "source_url": "https://source.test/item", "remote_url": "https://remote.test/image.jpg",
                                 "local_path": local_path, "status": "downloaded", "created_at": created})
    for block_id, position, asset_id in (("block_first", 0, "asset_late"), ("block_second", 1, "asset_early"),
                                         ("block_third", 2, "asset_abs"), ("block_fourth", 3, "asset_remote")):
        local_path = next(row[1] for row in (
            ("asset_late", "card/image.jpg"), ("asset_early", "../secret"),
            ("asset_abs", "/tmp/secret"), ("asset_remote", "remote-only"),
        ) if row[0] == asset_id)
        block_repo.upsert_block({"block_id": block_id, "card_id": "card_0", "position": position,
                                 "layer": "media", "block_kind": "image", "status": "ready",
                                 "source_url": "https://remote.test/image.jpg", "body": local_path,
                                 "raw_payload": {"asset_id": asset_id}})
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("update tasks set error_reason=?, last_failure_class='provider_error' where task_id='build_0'",
                     ("token=abc /Users/me/private/file ../secret",))
    item = client.get(_trace_path(client)).json()["item_page"]["items"][0]
    media = item["media_branches"]
    assert [row["block_id"] for row in media] == ["block_first", "block_second", "block_third", "block_fourth"]
    assert media[0]["media_src"] == "/api/assets/card/image.jpg"
    assert [row["media_src"] for row in media[1:]] == [None, None, None]
    assert media[-1]["source_link"] == "https://remote.test/image.jpg"
    assert "abc" not in json.dumps(item)
    assert "/Users" not in json.dumps(item)
    assert item["final_card"]["projection_semantics"] == "当前 Card 投影，非运行时快照"


def test_exact_products_reject_stale_archived_and_separate_current_topics(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    parent = "build_0"
    tasks = [
        Task("summary_exact", TaskType.SUMMARY, "card", "card_0", TaskStatus.SUCCESS,
             (DependencySpec(parent, TaskStatus.SUCCESS),), artifacts_summary={"chain_id": "chain_1"},
             input_version="summary-v", result_version="summary-v", started_at=NOW, finished_at=NOW),
        Task("decide_stale", TaskType.DECIDE, "card", "card_0", TaskStatus.SUCCESS,
             (DependencySpec("summary_exact", TaskStatus.SUCCESS),),
             artifacts_summary={"chain_id": "chain_1", "decision_id": "decision_stale"},
             input_version="decision-v", result_version="decision-v", started_at=NOW, finished_at=NOW),
        Task("output_archived", TaskType.OUTPUT_GENERATE, "card", "card_0", TaskStatus.SUCCESS,
             (DependencySpec("decide_stale", TaskStatus.SUCCESS),),
             artifacts_summary={"chain_id": "chain_1", "artifact_id": "output_old", "decision_id": "decision_stale"},
             input_version="output-v", result_version="output-v", started_at=NOW, finished_at=NOW),
        Task("topic_history", TaskType.TOPIC_UPDATE, "card", "card_0", TaskStatus.SUCCESS,
             (DependencySpec("output_archived", TaskStatus.SUCCESS),),
             artifacts_summary={"chain_id": "chain_1", "topic_ids": ["topic_old"]},
             input_version="topic-v", result_version="topic-v", started_at=NOW, finished_at=NOW),
    ]
    for task in tasks:
        repo.enqueue(task)
    _link(tmp_path, "build_0", "summary_exact")
    _link(tmp_path, "summary_exact", "decide_stale")
    _link(tmp_path, "decide_stale", "output_archived")
    _link(tmp_path, "output_archived", "topic_history")
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            """insert into card_ai_insight_versions
               (card_id,input_version,main_point,topic_assignment,scores,score_reasons,why_it_matters,
                is_time_sensitive,evidence_quotes,status,model,prompt_version,generated_at)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("card_0", "summary-v", "exact point", None, "{}", "{}", "exact why", 0, "[]",
             "succeeded", "model", "prompt", NOW),
        )
        conn.execute(
            """insert into decision_records
               (decision_id,target_kind,target_id,route,reason,evidence_refs,status,created_at,updated_at)
               values (?,?,?,?,?,?,?,?,?)""",
            ("decision_stale", "card", "another_card", "archive", "stale", "[]", "open", NOW, NOW),
        )
        conn.execute(
            """insert into output_artifacts
               (artifact_id,artifact_type,source_decision_id,title,body,status,created_at,updated_at)
               values (?,?,?,?,?,?,?,?)""",
            ("output_old", "task", "decision_stale", "old", "{}", "archived", NOW, NOW),
        )
        conn.execute(
            """insert into topics
               (topic_id,title,updated_at,created_at) values (?,?,?,?)""",
            ("topic_current", "Current topic", NOW, NOW),
        )
        conn.execute(
            "insert into topic_cards (topic_id,card_id,position,created_at) values (?,?,?,?)",
            ("topic_current", "card_0", 0, NOW),
        )
    item = client.get(_trace_path(client)).json()["item_page"]["items"][0]
    by_task = {stage["task_id"]: stage for stage in item["stages"]}
    for task_id in ("summary_exact", "decide_stale", "output_archived", "topic_history"):
        assert _ta(task_id) not in by_task
    assert "current_topics" not in item["final_card"]


def test_query_budget_is_constant_for_list_and_trace(tmp_path, monkeypatch):
    _seed_run(tmp_path, raw_keys=tuple(("connector_a", f"raw_{index:03}") for index in range(30)))
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "media_budget", TaskType.MEDIA_DOWNLOAD, "card", "card_0", TaskStatus.SUCCESS,
        (DependencySpec("build_0", TaskStatus.SUCCESS),),
        artifacts_summary={"chain_id": "chain_1", "assets": [
            {"asset_id": "asset_budget", "asset_kind": "image"}
        ]},
    ))
    _link(tmp_path, "build_0", "media_budget")
    CardAssetRepository(tmp_path).upsert_asset({
        "asset_id": "asset_budget", "card_id": "card_0", "asset_kind": "image",
        "source_url": "https://assets.test/budget", "status": "downloaded",
    })
    import backend.infrastructure.storage.source_run_trace_queries as queries
    from backend.infrastructure.storage.database import connect_existing as real_connect_existing
    from backend.read_models.source_run_trace import read_source_run_trace, read_source_runs

    statements = []

    @contextmanager
    def counted_connect(path):
        with real_connect_existing(path) as conn:
            conn.set_trace_callback(lambda statement: statements.append(statement))
            yield conn

    monkeypatch.setattr(queries, "connect_existing", counted_connect)
    read_source_runs(tmp_path, limit=10, offset=0)
    list_queries = len(statements)
    statements.clear()
    read_source_run_trace(tmp_path, _ta("fetch_1"), item_limit=25, cursor=None)
    trace_queries = len(statements)
    assert list_queries <= 5
    assert trace_queries <= 16


def test_trace_graph_is_bounded_and_excludes_foreign_children(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    for index in range(300):
        repo.enqueue(Task(f"unrelated_{index}", TaskType.CONTENT_ENRICH, "card", f"other_{index}",
                          artifacts_summary={"chain_id": "unrelated"}))
    repo.enqueue(Task(
        "foreign_child", TaskType.CONTENT_ENRICH, "card", "card_0",
        depends_on=(DependencySpec("build_0", TaskStatus.SUCCESS),),
        artifacts_summary={"chain_id": "foreign_chain"},
    ))
    _link(tmp_path, "build_0", "foreign_child", "lineage_child")
    repo.enqueue(Task(
        "lineage_child", TaskType.CONTENT_ENRICH, "card", "card_0",
        depends_on=(DependencySpec("build_0", TaskStatus.SUCCESS),),
        artifacts_summary={"chain_id": "chain_1"},
    ))
    payload = client.get(_trace_path(client)).json()
    stages = payload["item_page"]["items"][0]["stages"]
    assert [stage["task_id"] for stage in stages] == [_ta("build_0"), _ta("lineage_child")]
    assert len(stages) == 2
    encoded = json.dumps(payload)
    assert "foreign_child" not in encoded
    assert "unrelated_" not in encoded


def test_legacy_raw_identity_never_uses_current_source_connector(tmp_path):
    client = _seed_run(tmp_path)
    db = resolve_sqlite_path(tmp_path)
    with connect(db) as conn:
        checkpoint = json.loads(conn.execute(
            "select payload from acquisition_checkpoints where task_id='fetch_1'"
        ).fetchone()[0])
        checkpoint.pop("raw_item_keys", None)
        checkpoint.pop("connector_id", None)
        conn.execute("update acquisition_checkpoints set payload=? where task_id='fetch_1'", (json.dumps(checkpoint),))
        conn.execute("update tasks set target_id='raw_1' where task_id='build_0'")
    payload = client.get(_trace_path(client)).json()
    assert payload["item_page"]["item_total"] == 0

    with connect(db) as conn:
        checkpoint["connector_id"] = "connector_a"
        checkpoint["raw_item_ids"] = ["raw_1", "raw_1"]
        conn.execute("update acquisition_checkpoints set payload=? where task_id='fetch_1'", (json.dumps(checkpoint),))
    item = client.get(_trace_path(client)).json()["item_page"]["items"][0]
    assert item["association"] == "provenance_unavailable"
    assert item["card_id"] == "unavailable"


def test_fetch_lifecycle_does_not_use_commit_status_or_status_only_execution(tmp_path):
    client = _seed_run(tmp_path)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("""update tasks set status='failed', started_at=null, finished_at=null,
                        claimed_by=null where task_id in ('fetch_1','build_0')""")
        conn.execute("update run_history set status='success', started_at=?, finished_at=? where run_id='commit_1'",
                     (NOW, NOW))
    payload = client.get(_trace_path(client)).json()
    assert payload["run"]["status"] == "attention"
    assert payload["run"]["execution_status"] == "failed"
    assert payload["run"]["started_at"] is None and payload["run"]["finished_at"] is None
    assert payload["run"]["commit_evidence"]["status"] == "success"
    item = payload["item_page"]["items"][0]
    assert item["execution"] == "execution_evidence_unavailable"


def test_valid_output_is_normalized_and_requires_exact_decision_lineage(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "decide_valid", TaskType.DECIDE, "card", "card_0", TaskStatus.SUCCESS,
        (DependencySpec("build_0", TaskStatus.SUCCESS),),
        artifacts_summary={"chain_id": "chain_1", "decision_id": "decision_valid"},
        input_version="decision-v", result_version="decision-v", started_at=NOW, finished_at=NOW,
    ))
    repo.enqueue(Task(
        "output_valid", TaskType.OUTPUT_GENERATE, "card", "card_0", TaskStatus.SUCCESS,
        (DependencySpec("decide_valid", TaskStatus.SUCCESS),),
        artifacts_summary={"chain_id": "chain_1", "decision_id": "decision_valid", "artifact_id": "output_valid"},
        input_version="output-v", result_version="output-v", started_at=NOW, finished_at=NOW,
    ))
    _link(tmp_path, "build_0", "decide_valid")
    _link(tmp_path, "decide_valid", "output_valid")
    body = {
        "signal": "signal", "why_it_matters": "why", "next_action": "act",
        "evidence": [{"card_id": "card_0", "quote": "quote",
                      "source_url": "https://www.youtube.com/watch?v=demoYt00002&token=masked&X-Amz-Signature=masked#fragment"}],
        "api_key": "masked-extra-field",
    }
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("""insert into decision_records
                       (decision_id,target_kind,target_id,route,reason,evidence_refs,status,created_at,updated_at)
                       values (?,?,?,?,?,?,?,?,?)""",
                     ("decision_valid", "card", "card_0", "review", "reason", "[]", "open", NOW, NOW))
        conn.execute("""insert into output_artifacts
                       (artifact_id,artifact_type,source_decision_id,title,body,status,created_at,updated_at)
                       values (?,?,?,?,?,?,?,?)""",
                     ("output_valid", "decision_brief", "decision_valid", "title", json.dumps(body), "draft", NOW, NOW))
    stages = client.get(_trace_path(client)).json()["item_page"]["items"][0]["stages"]
    assert all(stage["task_id"] not in {_ta("output_valid"), _ta("decide_valid")} for stage in stages)
    assert client.get(_trace_path(client)).json()["retired_steps"] == {"count": 2, "availability": "available"}


def test_production_media_mapping_filters_machine_blocks_and_deduplicates_assets(tmp_path):
    client = _seed_run(tmp_path)
    asset_repo = CardAssetRepository(tmp_path)
    block_repo = CardContentBlockRepository(tmp_path)
    asset_repo.upsert_asset({"asset_id": "asset_prod", "card_id": "card_0", "asset_kind": "image",
                             "source_url": "https://source.test/post", "remote_url": "https://cdn.test/i.jpg?sig=masked",
                             "local_path": "card_0/generic/i.jpg", "status": "downloaded", "created_at": NOW})
    asset_repo.upsert_asset({"asset_id": "asset_legacy", "card_id": "card_0", "asset_kind": "video",
                             "source_url": "https://source.test/post", "local_path": "card_0/x/v.mp4",
                             "status": "downloaded", "created_at": "2026-07-14T09:00:00Z"})
    block_repo.upsert_block({"block_id": "media_raw_id", "card_id": "card_0", "position": 2,
                             "layer": "media", "block_kind": "image", "status": "downloaded",
                             "body": "card_0/generic/i.jpg", "source_url": "https://cdn.test/i.jpg?token=masked",
                             "raw_payload": {"asset_id": "asset_prod", "strategy": "direct_candidate"}})
    block_repo.upsert_block({"block_id": "media_duplicate", "card_id": "card_0", "position": 3,
                             "layer": "media", "block_kind": "image", "status": "downloaded",
                             "body": "card_0/generic/i.jpg", "source_url": "https://cdn.test/i.jpg"})
    block_repo.upsert_block({"block_id": "media_legacy_body", "card_id": "card_0", "position": 1,
                             "layer": "media", "block_kind": "video", "status": "downloaded",
                             "body": "card_0/x/v.mp4", "source_url": "https://source.test/post"})
    block_repo.upsert_block({"block_id": "machine_gap", "card_id": "card_0", "position": 4,
                             "layer": "llm_context", "block_kind": "media_download_gap", "status": "failed",
                             "body": "internal path /tmp/private"})
    item = client.get(_trace_path(client)).json()["item_page"]["items"][0]
    assert [media["block_id"] for media in item["media_branches"]] == ["media_legacy_body", "media_raw_id"]
    assert item["media_branches"][0]["media_src"] == "/api/assets/card_0/x/v.mp4"
    assert item["media_branches"][1]["media_src"] == "/api/assets/card_0/generic/i.jpg"
    final_blocks = {block["block_id"]: block for block in item["final_card"]["blocks"]}
    assert "machine_gap" not in final_blocks
    assert "body" not in final_blocks["media_raw_id"]
    assert "raw_payload" not in json.dumps(item)


def test_unified_url_and_free_text_sanitization(tmp_path):
    client = _seed_run(tmp_path)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        checkpoint = json.loads(conn.execute(
            "select payload from acquisition_checkpoints where task_id='fetch_1'"
        ).fetchone()[0])
        checkpoint.update({"provider": "Bearer masked-provider", "cursor_before": "Cookie: sid=masked",
                           "cursor_after": "api_key=masked"})
        conn.execute("update acquisition_checkpoints set payload=? where task_id='fetch_1'", (json.dumps(checkpoint),))
        conn.execute("update raw_items set source_url=? where connector_id='connector_a' and raw_item_id='raw_1'",
                     ("https://user:masked@example.test/private?X-Amz-Signature=masked#fragment",))
        conn.execute("update cards set source_url=? where card_id='card_0'",
                     ("https://example.test/card?token=masked#fragment",))
    payload = client.get(_trace_path(client)).json()
    assert payload["acquisition"]["provider"] == "[REDACTED]"
    assert "[REDACTED]" in payload["acquisition"]["cursor_before"]
    assert "masked" not in payload["acquisition"]["cursor_after"]
    item = payload["item_page"]["items"][0]
    assert item["current_projection_url"] == "https://example.test/card"


@pytest.mark.parametrize("status", ["success", "failed", "pending", "blocked"])
def test_root_fetch_error_and_retry_projection(tmp_path, status):
    client = _seed_run(tmp_path)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            "update tasks set status=?, attempt=2, max_attempts=3, next_run_at=?, "
            "error_reason=?, last_failure_class=? where task_id='fetch_1'",
            (status, NOW, None if status == "success" else "Fetch timeout token=synthetic-secret /tmp/private/file",
             None if status == "success" else "network_timeout"),
        )
    run = client.get(_trace_path(client)).json()["run"]
    assert run["max_attempts"] == 3
    assert run["retry_at"] == (NOW if status == "pending" else None)
    if status == "success":
        assert run["error"] is None
    else:
        assert run["error"]["summary"] == "Fetch timeout token=[REDACTED] [PATH]"
        assert run["error"]["class"] == "network_timeout"
