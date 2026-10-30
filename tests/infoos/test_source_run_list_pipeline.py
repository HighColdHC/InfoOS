from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from backend.application.tasks.planner import raw_item_target_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.infrastructure.storage.database import connect
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.repositories import RunRepository
from backend.infrastructure.storage import source_run_list_queries
from backend.infrastructure.storage.source_run_trace_queries import LIST_PAGE_NODE_LIMIT, SourceRunTraceQueries
from backend.infrastructure.storage.source_run_trace_queries import BATCH_IDS_PER_FETCH_LIMIT
from backend.read_models import source_run_list as source_run_list_module
from backend.read_models.source_run_trace_safety import source_identifier_fields, task_identifier_alias
from tests.infoos._factories import make_supported_source_payload
from tests.infoos.test_source_run_trace_api import NOW, _link, _seed_run, _ta, _trace_path
from tests.infoos.test_source_run_trace_api import _seed_xhs_acceptance_run


BUCKETS = [
    "fetch", "card_build", "content_enrich", "media", "card_finalize",
]


def test_source_run_page_filters_before_pagination_and_returns_collector_summary(tmp_path):
    client = _seed_xhs_acceptance_run(tmp_path)

    response = client.get(
        "/api/source-runs",
        params={
            "query": "小红书",
            "platform": "xiaohongshu",
            "status": "attention",
            "limit": 1,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {
        "last_24h": 0,
        "completed": 0,
        "attention": 1,
        "active": 0,
    }
    assert payload["total"] == 1
    assert payload["next_offset"] is None
    item = payload["items"][0]
    assert item["platform"] == "xiaohongshu"
    assert item["status"] == "attention"
    assert item["source_name"] == "小红书测试账号"
    assert item["fetch_result"] == {
        "total_links": 10,
        "dedup_skipped": 3,
        "pending_items": 7,
        "raw_item_count": 7,
    }
    assert item["card_progress"] == {
        "total": 7,
        "complete": 6,
        "partial": 1,
        "failed": 0,
    }
    assert "ai_summary" not in item
    assert [bucket["bucket"] for bucket in item["pipeline"]["buckets"]] == BUCKETS


def test_source_run_page_filters_execution_scope_and_preserves_execution_timestamps(tmp_path):
    client = _seed_run(tmp_path)
    now = datetime.now(timezone.utc)
    due_at = (now - timedelta(minutes=5)).isoformat(timespec="seconds").replace("+00:00", "Z")
    future_at = (now + timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "fetch_due", TaskType.FETCH, "source", "source_1", TaskStatus.PENDING,
        next_run_at=due_at,
    ))
    repo.enqueue(Task(
        "fetch_future", TaskType.FETCH, "source", "source_1", TaskStatus.PENDING,
        next_run_at=future_at,
        artifacts_summary={"scheduled_for": future_at},
    ))

    executed = client.get("/api/source-runs", params={"execution_scope": "executed", "limit": 100})
    upcoming = client.get("/api/source-runs", params={"execution_scope": "upcoming", "limit": 100})

    assert executed.status_code == 200
    assert upcoming.status_code == 200
    executed_items = executed.json()["items"]
    upcoming_items = upcoming.json()["items"]
    assert {item["execution_bucket"] for item in executed_items} == {"executed"}
    assert {item["execution_bucket"] for item in upcoming_items} == {"due", "future"}
    completed = executed_items[0]
    assert completed["scheduled_at"] == NOW
    assert completed["started_at"] == NOW
    assert completed["finished_at"] == NOW
    by_bucket = {item["execution_bucket"]: item for item in upcoming_items}
    assert by_bucket["due"]["started_at"] is None
    assert by_bucket["due"]["finished_at"] is None
    assert by_bucket["future"]["scheduled_at"] == future_at

    invalid = client.get("/api/source-runs", params={"execution_scope": "later"})
    assert invalid.status_code == 422


def test_source_run_page_filters_task_view_in_sql_before_stats_and_pagination(tmp_path):
    client = _seed_run(tmp_path)
    now = datetime.now(timezone.utc)
    collection_planned_at = (now + timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    research_planned_at = (now + timedelta(hours=2)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            "update sources set next_run_at=? where source_id='source_1'",
            (collection_planned_at,),
        )
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="source_research",
        platform="zsxq",
        source_type="keyword",
        identifier="AI agent",
        connector_id="connector_research",
        display_name="Research Source",
        next_run_at=research_planned_at,
    ))
    TaskRepository.for_base_dir(tmp_path).enqueue(Task(
        "fetch_research", TaskType.FETCH, "source", "source_research",
        TaskStatus.SUCCESS, started_at=NOW, finished_at=NOW,
    ))

    all_payload = client.get("/api/source-runs", params={"limit": 100}).json()
    collection = client.get(
        "/api/source-runs", params={"task_view": "collection", "limit": 1}
    ).json()
    research = client.get(
        "/api/source-runs", params={"task_view": "research", "limit": 1}
    ).json()
    collection_all = client.get(
        "/api/source-runs", params={"task_view": "collection", "limit": 100}
    ).json()
    research_all = client.get(
        "/api/source-runs", params={"task_view": "research", "limit": 100}
    ).json()

    assert all_payload["total"] == 4
    assert collection["total"] == research["total"] == 2
    assert collection["next_offset"] == research["next_offset"] == 1
    assert collection["summary"] == {
        "last_24h": 0, "completed": 0, "attention": 1, "active": 1,
    }
    assert research["summary"] == {
        "last_24h": 0, "completed": 0, "attention": 1, "active": 1,
    }
    collection_ids = {item["source_run_id"] for item in collection_all["items"]}
    research_ids = {item["source_run_id"] for item in research_all["items"]}
    all_ids = {item["source_run_id"] for item in all_payload["items"]}
    assert collection_ids.isdisjoint(research_ids)
    assert collection_ids | research_ids == all_ids
    assert {item["entry_kind"] for item in collection_all["items"]} == {"run", "planned"}
    assert {item["entry_kind"] for item in research_all["items"]} == {"run", "planned"}
    assert {
        (item["source_type"], item["task_view"])
        for item in collection_all["items"]
    } == {("feed", "collection")}
    assert {
        (item["source_type"], item["task_view"])
        for item in research_all["items"]
    } == {("keyword", "research")}

    invalid = client.get("/api/source-runs", params={"task_view": "discovery"})
    assert invalid.status_code == 422
    assert invalid.json() == {
        "detail": "task_view must be all, collection or research",
    }


def test_research_run_list_progress_includes_every_reused_card_pipeline(tmp_path):
    raw_keys = tuple(("connector_a", f"raw_{index}") for index in range(80))
    client = _seed_run(tmp_path, raw_keys=raw_keys)
    repo = TaskRepository.for_base_dir(tmp_path)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            "update sources set platform='zsxq', source_type='keyword' "
            "where source_id='source_1'"
        )
    downstream_by_build = {}
    for index in range(80):
        finalize_id = f"finalize_{index}"
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
                "completeness_status": "complete" if index < 68 else "partial",
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

    response = client.get(
        "/api/source-runs",
        params={"task_view": "research", "limit": 25},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["fetch_result"]["total_links"] == 80
    assert payload["items"][0]["card_progress"] == {
        "total": 80,
        "complete": 68,
        "partial": 12,
        "failed": 0,
    }
    assert payload["summary"]["completed"] == 0
    assert payload["summary"]["attention"] == 1
    assert payload["items"][0]["status"] == "attention"

    completed = client.get(
        "/api/source-runs",
        params={"task_view": "research", "status": "completed"},
    ).json()
    attention = client.get(
        "/api/source-runs",
        params={"task_view": "research", "status": "attention"},
    ).json()

    assert completed["total"] == 0
    assert completed["items"] == []
    assert completed["summary"]["completed"] == 0
    assert completed["summary"]["attention"] == 1
    assert attention["total"] == 1
    assert attention["items"][0]["status"] == "attention"
    assert attention["items"][0]["card_progress"] == payload["items"][0]["card_progress"]


def test_research_run_status_uses_all_roots_beyond_list_projection_limit(tmp_path):
    raw_keys = tuple(("connector_a", f"raw_{index}") for index in range(80))
    client = _seed_run(tmp_path, raw_keys=raw_keys)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            "update sources set platform='zsxq', source_type='keyword' "
            "where source_id='source_1'"
        )
        conn.execute(
            "update tasks set status='pending', started_at=null, finished_at=null "
            "where task_id='build_70'"
        )

    all_payload = client.get(
        "/api/source-runs",
        params={"task_view": "research"},
    ).json()
    pending = client.get(
        "/api/source-runs",
        params={"task_view": "research", "status": "pending"},
    ).json()
    attention = client.get(
        "/api/source-runs",
        params={"task_view": "research", "status": "attention"},
    ).json()

    assert all_payload["summary"]["active"] == 1
    assert all_payload["summary"]["attention"] == 0
    assert all_payload["items"][0]["status"] == "pending"
    assert pending["total"] == 1
    assert pending["total_exact"] is True
    assert pending["counts_exact"] is False
    assert pending["items"][0]["status"] == "pending"
    assert attention["total"] == 0
    assert attention["items"] == []


def test_source_run_page_upcoming_includes_known_occurrences_beyond_24_hours(tmp_path):
    client = _seed_run(tmp_path)
    now = datetime.now(timezone.utc)
    far_fetch_at = (now + timedelta(days=3)).isoformat(timespec="seconds").replace("+00:00", "Z")
    far_planned_at = (now + timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "fetch_far_future", TaskType.FETCH, "source", "source_1", TaskStatus.PENDING,
        next_run_at=far_fetch_at,
        artifacts_summary={"scheduled_for": far_fetch_at},
    ))
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="source_far_future",
        connector_id="connector_far_future",
        display_name="Far Future Source",
        next_run_at=far_planned_at,
    ))

    response = client.get(
        "/api/source-runs", params={"execution_scope": "upcoming", "limit": 100}
    )

    assert response.status_code == 200
    future_times = {item["scheduled_at"] for item in response.json()["items"]}
    assert far_fetch_at in future_times
    assert far_planned_at in future_times


def test_source_run_page_filters_sql_product_status_without_changing_summary(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "finalize_0", TaskType.CARD_FINALIZE, "card", "card_0", TaskStatus.SUCCESS,
        (DependencySpec("build_0", TaskStatus.SUCCESS),),
        artifacts_summary={
            "chain_id": "chain_1", "card_id": "card_0",
            "completeness_status": "complete", "card_version": 1,
        },
        started_at=NOW,
        finished_at=NOW,
    ))
    _link(tmp_path, "build_0", "finalize_0")

    completed = client.get("/api/source-runs", params={"status": "completed"}).json()
    attention = client.get("/api/source-runs", params={"status": "attention"}).json()

    assert completed["total"] == 1
    assert completed["items"][0]["status"] == "completed"
    assert "product_status" not in completed["items"][0]
    assert completed["summary"]["completed"] == 1
    assert attention["total"] == 0
    assert attention["items"] == []
    assert attention["summary"]["completed"] == 1


def test_source_run_page_ignores_complete_finalize_from_foreign_referenced_root(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "foreign_build", TaskType.CARD_BUILD, "raw_item",
        raw_item_target_id("connector_a", "foreign"), TaskStatus.SUCCESS,
        artifacts_summary={"chain_id": "foreign_chain", "card_id": "foreign_card"},
    ))
    repo.enqueue(Task(
        "foreign_finalize", TaskType.CARD_FINALIZE, "card", "foreign_card", TaskStatus.SUCCESS,
        (DependencySpec("foreign_build", TaskStatus.SUCCESS),),
        artifacts_summary={
            "chain_id": "foreign_chain", "card_id": "foreign_card",
            "completeness_status": "complete", "card_version": 1,
        },
        started_at=NOW,
        finished_at=NOW,
    ))
    _link(tmp_path, "foreign_build", "foreign_finalize")
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        checkpoint = json.loads(conn.execute(
            "select payload from acquisition_checkpoints where task_id='fetch_1'"
        ).fetchone()[0])
        checkpoint["card_build_task_ids"].append("foreign_build")
        conn.execute(
            "update acquisition_checkpoints set payload=? where task_id='fetch_1'",
            (json.dumps(checkpoint),),
        )

    completed = client.get("/api/source-runs", params={"status": "completed"}).json()
    attention = client.get("/api/source-runs", params={"status": "attention"}).json()

    assert completed["total"] == 0
    assert completed["items"] == []
    assert attention["total"] == 1
    item = attention["items"][0]
    assert item["status"] == "attention"
    assert item["card_progress"] == {
        "total": 0, "complete": 0, "partial": 0, "failed": 0,
    }
    assert item["pipeline"]["referenced_item_count"] == 1
    assert item["issues"] == []


def test_source_run_page_expands_pipeline_only_for_selected_page(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    for index in range(220):
        repo.enqueue(Task(
            f"fetch_scale_{index:03}", TaskType.FETCH, "source", "source_1",
            TaskStatus.SUCCESS, started_at=NOW, finished_at=NOW,
        ))
    response = client.get("/api/source-runs", params={"limit": 7, "offset": 14})

    assert response.status_code == 200
    assert response.json()["counts_exact"] is False
    assert response.json()["total"] >= 22
    assert [item["root_task_id"] for item in response.json()["items"]] == [
        task_identifier_alias(f"fetch_scale_{index:03}") for index in range(13, 20)
    ]


def test_source_run_keyset_matches_full_order_across_tied_timestamp_batch_boundary(
    tmp_path, monkeypatch,
):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    for index in range(105):
        repo.enqueue(Task(
            f"fetch_tied_{index:03}", TaskType.FETCH, "source", "source_1",
            TaskStatus.SUCCESS, started_at=NOW, finished_at=NOW,
        ))

    complete = client.get("/api/source-runs", params={"limit": 100, "offset": 0}).json()
    classified_sizes = []
    original = source_run_list_module.load_source_run_catalog_page.__globals__[
        "_load_source_run_catalog_batch"
    ]

    def capture(*args, **kwargs):
        classified_sizes.append(len(kwargs["selected_candidates"]))
        return original(*args, **kwargs)

    monkeypatch.setitem(
        source_run_list_module.load_source_run_catalog_page.__globals__,
        "_load_source_run_catalog_batch", capture,
    )
    boundary = client.get("/api/source-runs", params={"limit": 10, "offset": 100}).json()

    assert boundary["counts_exact"] is False
    assert boundary["total"] == complete["total"] == 106
    assert [item["root_task_id"] for item in boundary["items"]] == [
        task_identifier_alias(f"fetch_tied_{index:03}") for index in range(99, 105)
    ]
    assert boundary["summary"]["attention"] == len(boundary["items"])


def test_catalog_join_order_matches_previous_sql_at_tied_page_and_filter_boundaries(
    tmp_path, monkeypatch,
):
    _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([
        Task(f"fetch_equivalent_{index:03}", TaskType.FETCH, "source", "source_1",
             TaskStatus.SUCCESS, started_at=NOW, finished_at=NOW,
             artifacts_summary={"scheduled_for": NOW})
        for index in range(41)
    ])
    original_connect = source_run_list_queries.connect_existing

    @contextmanager
    def previous_join_order(path):
        with original_connect(path) as conn:
            class ConnectionView:
                @property
                def in_transaction(self):
                    return conn.in_transaction

                def execute(self, sql, params=()):
                    return conn.execute(
                        sql.replace("cross join tasks root_task", "join tasks root_task")
                           .replace("cross join tasks task", "join tasks task"),
                        params,
                    )

            yield ConnectionView()

    common = dict(now="2026-09-28T05:00:00Z", last_24h_start="2026-09-27T05:00:00Z",
                  time_from=None, time_to=None, query=None, platform=None,
                  status=None, execution_scope="all", task_view="all")
    cases = [
        dict(limit=7, offset=0),
        dict(limit=7, offset=35),
        dict(limit=7, offset=7, status="attention"),
        dict(limit=7, offset=7, platform="rss", task_view="collection",
             execution_scope="executed", time_from="2026-01-01T00:00:00Z",
             time_to="2026-12-31T23:59:59Z"),
        dict(limit=7, offset=0, query="source_1"),
    ]
    for case in cases:
        options = common | case
        current = source_run_list_queries.load_source_run_catalog_page(tmp_path, **options)
        with monkeypatch.context() as patch:
            patch.setattr(source_run_list_queries, "connect_existing", previous_join_order)
            previous = source_run_list_queries.load_source_run_catalog_page(tmp_path, **options)
        assert current == previous


def test_recent_run_limit_matches_full_result_with_tied_times_and_filters(tmp_path):
    _seed_run(tmp_path)
    sources = SourceRepository(tmp_path)
    sources.upsert_source(make_supported_source_payload(
        source_id="source_other", connector_id="connector_other",
        display_name="Other Source",
    ))
    repo = RunRepository(tmp_path)
    for index in range(8):
        repo.append_run({
            "run_id": f"run_equivalent_{index:02}",
            "source_id": "source_1" if index % 2 else "source_other",
            "scheduled_for": NOW, "started_at": NOW, "finished_at": NOW,
            "status": "success", "task_type": "fetch" if index % 3 else "summary",
        })
    for filters in ({}, {"source_id": "source_1"}, {"task_type": "summary"},
                    {"source_id": "source_other", "task_type": "fetch"}):
        all_rows = repo.list_runs(**filters)
        for limit in (0, 1, 3, len(all_rows), len(all_rows) + 1):
            assert repo.list_runs(**filters, limit=limit) == all_rows[:limit]


def test_source_run_filtered_pages_match_explicit_multi_source_reference(tmp_path):
    client = _seed_run(tmp_path)
    current = datetime.now(timezone.utc).replace(microsecond=0)
    scheduled = current.isoformat().replace("+00:00", "Z")
    planned = (current + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    time_from = (current - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    time_to = (current + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    sources = SourceRepository(tmp_path)
    sources.upsert_source(make_supported_source_payload(
        source_id="source_2", connector_id="connector_2", display_name="Second Source",
    ))
    sources.upsert_source(make_supported_source_payload(
        source_id="source_planned", connector_id="connector_planned",
        display_name="Planned Source", next_run_at=planned,
    ))
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("update sources set next_run_at=null where source_id in ('source_1','source_2')")
    repo = TaskRepository.for_base_dir(tmp_path)
    expected_ids = []
    for index in range(105):
        task_id = f"fetch_reference_{index:03}"
        expected_ids.append(task_identifier_alias(task_id))
        repo.enqueue(Task(
            task_id, TaskType.FETCH, "source", "source_1" if index % 2 == 0 else "source_2",
            TaskStatus.SUCCESS, started_at=scheduled, finished_at=scheduled,
            artifacts_summary={"scheduled_for": scheduled},
        ))
    repo.enqueue(Task(
        "fetch_reference_pending", TaskType.FETCH, "source", "source_2",
        TaskStatus.PENDING, next_run_at=scheduled, artifacts_summary={"scheduled_for": scheduled},
    ))

    actual_ids = []
    offset = 0
    last_page = None
    while True:
        response = client.get("/api/source-runs", params={
            "status": "attention", "task_view": "collection",
            "time_from": time_from, "time_to": time_to, "limit": 10, "offset": offset,
        })
        assert response.status_code == 200
        last_page = response.json()
        actual_ids.extend(item["root_task_id"] for item in last_page["items"])
        if last_page["next_offset"] is None:
            break
        offset = last_page["next_offset"]

    assert actual_ids == expected_ids
    assert last_page is not None and last_page["counts_exact"] is True
    assert last_page["total"] == 105
    assert last_page["summary"] == {
        "last_24h": 105, "completed": 0, "attention": 105, "active": 2,
    }


def test_source_run_batches_share_snapshot_during_concurrent_insert(tmp_path, monkeypatch):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([
        Task(f"fetch_snapshot_{index:03}", TaskType.FETCH, "source", "source_1",
             TaskStatus.SUCCESS, started_at=NOW, finished_at=NOW)
        for index in range(101)
    ])
    original = source_run_list_module.load_source_run_catalog_page.__globals__[
        "_load_source_run_catalog_batch"
    ]
    connections = []

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        connections.append((id(kwargs["conn"]), kwargs["conn"].in_transaction))
        if len(connections) == 1:
            repo.enqueue(Task(
                "fetch_inserted_between_batches", TaskType.FETCH, "source", "source_1",
                TaskStatus.SUCCESS, started_at=NOW, finished_at=NOW,
            ))
        return result

    monkeypatch.setitem(
        source_run_list_module.load_source_run_catalog_page.__globals__,
        "_load_source_run_catalog_batch", capture,
    )
    payload = client.get(
        "/api/source-runs", params={"status": "completed", "limit": 1}
    ).json()

    assert len(connections) >= 2
    assert len({connection_id for connection_id, _active in connections}) == 1
    assert all(active for _connection_id, active in connections)
    assert payload["counts_exact"] is True
    assert payload["total"] == 0


def test_source_run_task_id_with_planned_prefix_and_invalid_time_keep_legacy_semantics(tmp_path):
    client = _seed_run(tmp_path)
    TaskRepository.for_base_dir(tmp_path).enqueue(Task(
        "planned:real-fetch", TaskType.FETCH, "source", "source_1", TaskStatus.SUCCESS,
        artifacts_summary={"scheduled_for": "invalid-time"},
    ))
    alias = task_identifier_alias("planned:real-fetch")

    unfiltered = client.get("/api/source-runs", params={"limit": 100}).json()
    bounded = client.get(
        "/api/source-runs", params={"time_to": "2999-01-01T00:00:00Z", "limit": 100}
    ).json()

    assert alias in {item["root_task_id"] for item in unfiltered["items"]}
    assert alias not in {item["root_task_id"] for item in bounded["items"]}


def test_planned_row_is_suppressed_by_committed_run_schedule_override(tmp_path):
    client = _seed_run(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        run_id = conn.execute(
            "select run_id from acquisition_checkpoints where task_id='fetch_1'"
        ).fetchone()[0]
        conn.execute("update run_history set scheduled_for=? where run_id=?", (future, run_id))
        conn.execute("update sources set next_run_at=? where source_id='source_1'", (future,))

    payload = client.get(
        "/api/source-runs", params={"execution_scope": "upcoming", "limit": 100}
    ).json()

    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["counts_exact"] is True


def test_source_run_query_filter_still_matches_fetch_artifacts(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "fetch_artifact_search", TaskType.FETCH, "source", "source_1", TaskStatus.SUCCESS,
        artifacts_summary={"scheduled_for": NOW, "search_marker": "needle-42"},
        started_at=NOW, finished_at=NOW,
    ))

    payload = client.get(
        "/api/source-runs", params={"query": "needle-42", "limit": 100}
    ).json()

    assert [item["root_task_id"] for item in payload["items"]] == [
        task_identifier_alias("fetch_artifact_search")
    ]


def test_list_pipeline_contract_has_fixed_order_and_associated_counts(tmp_path):
    client = _seed_run(tmp_path)
    for kind in (TaskType.CARD_LLM_ENRICH, TaskType.IMAGE_UNDERSTAND):
        task = Task(f"retired_{kind.value}", kind, "card", "card_0", TaskStatus.FAILED,
                    (DependencySpec("build_0", TaskStatus.SUCCESS),),
                    artifacts_summary={"chain_id": "chain_1"})
        TaskRepository.for_base_dir(tmp_path).enqueue(task)
        _link(tmp_path, "build_0", task.task_id)
    item = client.get("/api/source-runs").json()["items"][0]
    pipeline = item["pipeline"]
    assert pipeline["evidence"] == "complete"
    assert pipeline["projection_truncated"] is False
    assert pipeline["referenced_item_count"] == 0
    assert pipeline["provenance_unavailable_count"] == 0
    assert [bucket["bucket"] for bucket in pipeline["buckets"]] == BUCKETS
    by_bucket = {bucket["bucket"]: bucket for bucket in pipeline["buckets"]}
    assert by_bucket["fetch"]["counts"] == {
        "pending": 0, "running": 0, "success": 1, "failed": 0,
        "blocked": 0, "skipped": 0, "total": 1,
    }
    assert by_bucket["card_build"]["counts"]["success"] == 1
    assert by_bucket["card_build"]["health"] == "succeeded"
    assert by_bucket["card_finalize"]["health"] == "not_present"
    assert item["issues"] == []
    assert by_bucket["media"]["counts"]["total"] == 0
    assert "card_llm_enrich" not in item.get("enrichment_stage", {})


def test_list_issues_only_use_durable_codes_and_referenced_is_not_counted(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    tasks = [
        Task("failed_summary", TaskType.SUMMARY, "card", "card_0", TaskStatus.FAILED,
             (DependencySpec("build_0", TaskStatus.SUCCESS),), artifacts_summary={"chain_id": "chain_1"}),
        Task("blocked_score", TaskType.SCORE, "card", "card_0", TaskStatus.BLOCKED,
             (DependencySpec("build_0", TaskStatus.SUCCESS),), artifacts_summary={"chain_id": "chain_1"}),
        Task("expired_media", TaskType.MEDIA_DOWNLOAD, "card", "card_0", TaskStatus.RUNNING,
             (DependencySpec("build_0", TaskStatus.SUCCESS),), lease_expires_at="2000-01-01T00:00:00Z",
             artifacts_summary={"chain_id": "chain_1"}),
        Task("retry_context", TaskType.CONTEXT_PACK, "card", "card_0", TaskStatus.PENDING,
             (DependencySpec("build_0", TaskStatus.SUCCESS),), next_run_at="2099-01-01T00:00:00Z",
             artifacts_summary={"chain_id": "chain_1"}),
        Task("waiting_semantic", TaskType.SEMANTIC_EXTRACT, "card", "card_0", TaskStatus.PENDING,
             (DependencySpec("build_0", TaskStatus.SUCCESS),), artifacts_summary={"chain_id": "chain_1"}),
        Task("skipped_topic", TaskType.TOPIC_UPDATE, "card", "card_0", TaskStatus.SKIPPED,
             (DependencySpec("build_0", TaskStatus.SUCCESS),), artifacts_summary={"chain_id": "chain_1"}),
        Task("referenced_build", TaskType.CARD_BUILD, "raw_item", raw_item_target_id("connector_a", "old"),
             TaskStatus.SUCCESS, artifacts_summary={"chain_id": "old_chain", "card_id": "card_0"}),
    ]
    for task in tasks:
        repo.enqueue(task)
    _link(tmp_path, "build_0", *(task.task_id for task in tasks[:-1]))
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        checkpoint = json.loads(conn.execute(
            "select payload from acquisition_checkpoints where task_id='fetch_1'"
        ).fetchone()[0])
        checkpoint["card_build_task_ids"].append("referenced_build")
        conn.execute("update acquisition_checkpoints set payload=? where task_id='fetch_1'", (json.dumps(checkpoint),))
    item = client.get("/api/source-runs").json()["items"][0]
    pipeline = item["pipeline"]
    assert pipeline["evidence"] == "complete"
    assert pipeline["referenced_item_count"] == 1
    by_bucket = {bucket["bucket"]: bucket for bucket in pipeline["buckets"]}
    assert by_bucket["card_build"]["counts"]["total"] == 1
    assert by_bucket["media"]["health"] == "running"
    codes = {issue["code"] for issue in item["issues"]}
    assert codes == {"expired_lease"}
    assert all(set(issue) == {"code", "severity", "count", "evidence"} for issue in item["issues"])


@pytest.mark.parametrize("failure_class", ["platform_not_logged_in", "source_unavailable"])
def test_list_blocking_reason_uses_safe_persisted_actionable_failure_class(tmp_path, failure_class):
    client = _seed_run(tmp_path)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            "update tasks set status='blocked', last_failure_class=? where task_id='fetch_1'",
            (failure_class,),
        )

    item = client.get("/api/source-runs").json()["items"][0]

    assert item["blocking_reason"] == failure_class
    assert {issue["code"] for issue in item["issues"]} == {"task_blocked"}


def test_list_blocking_reason_supports_browser_session_and_redacts_unknown_failure(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_id, failure_class in (
        ("blocked_browser", "browser_session_not_ready"),
        ("blocked_unsafe", "cookie=/Users/me/private/session"),
    ):
        repo.enqueue(Task(
            task_id, TaskType.CONTENT_ENRICH, "card", "card_0", TaskStatus.BLOCKED,
            (DependencySpec("build_0", TaskStatus.SUCCESS),),
            last_failure_class=failure_class,
            artifacts_summary={"chain_id": "chain_1", "chain_root_task_id": "fetch_1"},
        ))
    _link(tmp_path, "build_0", "blocked_browser", "blocked_unsafe")

    item = client.get("/api/source-runs").json()["items"][0]

    assert item["blocking_reason"] == "browser_session_not_ready"
    assert "/Users" not in json.dumps(item)


def test_list_projection_is_bounded_and_truncation_is_partial_not_issue(tmp_path, monkeypatch):
    client = _seed_run(tmp_path, raw_keys=tuple(("connector_a", f"raw_{index:03}") for index in range(90)))
    repo = TaskRepository.for_base_dir(tmp_path)
    for index in range(400):
        repo.enqueue(Task(f"unrelated_list_{index}", TaskType.SUMMARY, "card", f"other_{index}",
                          artifacts_summary={"chain_id": "foreign"}))
    for index in range(8):
        repo.enqueue(Task(f"fetch_extra_{index}", TaskType.FETCH, "source", "source_1"))
    captured = {}
    original = SourceRunTraceQueries.load_list_pipeline_rows

    def capture(self, rows):
        result = original(self, rows)
        captured.update(result)
        return result

    monkeypatch.setattr(SourceRunTraceQueries, "load_list_pipeline_rows", capture)
    items = client.get("/api/source-runs", params={"limit": 100}).json()["items"]
    assert len(items) == 9
    item = next(row for row in items if row["source_run_id"] == _ta("fetch_1"))
    assert item["pipeline"]["evidence"] == "partial"
    assert item["pipeline"]["projection_truncated"] is True
    assert item["pipeline"]["provenance_unavailable_count"] == 26
    assert "lineage_evidence_unavailable" not in {issue["code"] for issue in item["issues"]}
    assert captured["loaded_row_count"] <= LIST_PAGE_NODE_LIMIT
    assert "unrelated_list_" not in json.dumps(item)
    assert all(bucket["health"] != "succeeded" for bucket in item["pipeline"]["buckets"])


def test_list_evidence_issues_cover_missing_checkpoint_commit_and_lineage(tmp_path):
    client = _seed_run(tmp_path)
    db = resolve_sqlite_path(tmp_path)
    with connect(db) as conn:
        checkpoint = json.loads(conn.execute(
            "select payload from acquisition_checkpoints where task_id='fetch_1'"
        ).fetchone()[0])
        checkpoint["card_build_task_ids"].append("missing_root")
        conn.execute("update acquisition_checkpoints set payload=? where task_id='fetch_1'", (json.dumps(checkpoint),))
        conn.execute("delete from run_history where run_id='commit_1'")
    item = client.get("/api/source-runs").json()["items"][0]
    assert item["pipeline"]["evidence"] == "partial"
    codes = {issue["code"] for issue in item["issues"]}
    assert codes == {"commit_history_unavailable", "lineage_evidence_unavailable"}

    with connect(db) as conn:
        conn.execute("delete from acquisition_checkpoints where task_id='fetch_1'")
    unavailable = client.get("/api/source-runs").json()["items"][0]
    assert unavailable["pipeline"]["evidence"] == "unavailable"
    codes = {issue["code"] for issue in unavailable["issues"]}
    assert codes == {"checkpoint_unavailable", "commit_history_unavailable"}


def test_final_card_source_context_separates_current_config_and_card_object(tmp_path):
    client = _seed_run(tmp_path)
    db = resolve_sqlite_path(tmp_path)
    with connect(db) as conn:
        conn.execute("""update sources set platform='youtube', source_type='channel', display_name='Current Channel',
                      cadence_hours=6, identifier=? where source_id='source_1'""",
                     ("https://www.youtube.com/watch?v=demoYt00002&token=placeholder",))
        conn.execute("update cards set source_platform='x', source_type='post', source_author='Historical Author' where card_id='card_0'")
    context = client.get(_trace_path(client)).json()["item_page"]["items"][0]["final_card"]["source_context"]
    assert context["config_semantics"] == "current_source_config_not_runtime_snapshot"
    assert context["config_availability"] == "available"
    assert context["monitoring"] == {
        "platform": "youtube", "source_type": "channel", "display_name": "Current Channel",
        "cadence_hours": 6.0,
        "identifier_display": "https://www.youtube.com/watch?v=demoYt00002",
        "identifier_url": "https://www.youtube.com/watch?v=demoYt00002",
    }
    assert context["card_object_semantics"] == "current_card_projection"
    assert context["card_object"] == {
        "source_platform": "x", "source_type": "post", "source_author": "Historical Author",
    }

    with connect(db) as conn:
        conn.execute("update sources set identifier=? where source_id='source_1'", ("C:\\private\\source.json",))
    unsafe_identifier = client.get(_trace_path(client)).json()["item_page"]["items"][0]["final_card"]["source_context"]
    assert unsafe_identifier["monitoring"]["identifier_display"] == "unavailable"
    assert unsafe_identifier["monitoring"]["identifier_url"] == "unavailable"

    with connect(db) as conn:
        conn.execute("update tasks set target_id='missing_source' where task_id='fetch_1'")
    missing = client.get(_trace_path(client)).json()["item_page"]["items"][0]["final_card"]["source_context"]
    assert missing["config_availability"] == "unavailable"
    assert set(missing["monitoring"].values()) == {"unavailable"}
    assert missing["card_object"]["source_platform"] == "x"


def test_source_identifier_text_allows_only_explicit_reddit_subreddit_format():
    for value in ("private/config.json", "connector/path", r"\\server\share", "./config", "../config"):
        assert source_identifier_fields(value, platform="rss", source_type="feed") == (
            "unavailable", "unavailable"
        )
    assert source_identifier_fields("r/Python", platform="reddit", source_type="subreddit") == (
        "r/python", "unavailable"
    )
    for disguised in ("reddit/r/python", "r/python/private", "r/py-thon", "r/../python"):
        assert source_identifier_fields(disguised, platform="reddit", source_type="subreddit") == (
            "unavailable", "unavailable"
        )
    assert source_identifier_fields("openai", platform="x", source_type="keyword") == (
        "openai", "unavailable"
    )


def test_batch_ids_are_bounded_with_total_and_stable_order(tmp_path, monkeypatch):
    client = _seed_run(tmp_path)
    db = resolve_sqlite_path(tmp_path)
    with connect(db) as conn:
        for index in range(50):
            batch_id = f"batch_{index:03}"
            created_at = f"2026-01-01T00:{index:02}:00Z"
            conn.execute("""insert into task_batches
                           (batch_id,batch_type,status,source_ids,metadata,created_at,updated_at)
                           values (?,?,?,?,?,?,?)""",
                         (batch_id, "source_run", "queued", "[]", "{}", created_at, created_at))
            conn.execute("insert into task_batch_items (batch_id,task_id,created_at) values (?,?,?)",
                         (batch_id, "fetch_1", created_at))
    import backend.infrastructure.storage.source_run_trace_queries as queries
    original_rows = queries._rows
    returned_batch_rows = []

    def capture_rows(conn, sql, params=()):
        rows = original_rows(conn, sql, params)
        if "limited_batches" in sql:
            returned_batch_rows.append(len(rows))
        return rows

    monkeypatch.setattr(queries, "_rows", capture_rows)
    list_item = client.get("/api/source-runs").json()["items"][0]
    assert list_item["batch_ids_total"] == 50
    assert list_item["batch_ids_truncated"] is True
    assert list_item["batch_ids"] == [f"batch_{index:03}" for index in range(BATCH_IDS_PER_FETCH_LIMIT)]
    detail_run = client.get(_trace_path(client)).json()["run"]
    assert detail_run["batch_ids_total"] == 50
    assert detail_run["batch_ids_truncated"] is True
    assert detail_run["batch_ids"] == list_item["batch_ids"]
    assert returned_batch_rows and max(returned_batch_rows) <= BATCH_IDS_PER_FETCH_LIMIT + 1
