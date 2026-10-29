from __future__ import annotations

import json

import pytest

from backend.application.workflows.fetch_commit import commit_fetch_result
from backend.application.workflows.research import create_research_run
from backend.domain.research import ResearchRequestError
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.database import connect_existing
from tests.infoos.storage.migration_test_helpers import migrate_database_path
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import SourceCursorRepository, SourceRepository


def _payload(*, platforms: list[dict] | None = None) -> dict:
    return {
        "name": "AI 出海",
        "keywords": ["AI", "出海"],
        "date_range": None,
        "platforms": platforms
        or [
            {"platform": "x", "max_items": 10, "sort": "latest", "options": {}},
            {"platform": "github", "max_items": 10, "sort": "latest", "options": {}},
        ],
    }


def test_create_research_run_atomically_persists_sources_roots_and_batch(tmp_path) -> None:
    run_id, replayed = create_research_run(
        tmp_path,
        payload=_payload(),
        idempotency_key="request-1",
    )

    assert replayed is False
    db_path = resolve_sqlite_path(tmp_path)
    with connect_existing(db_path) as conn:
        batch = conn.execute(
            "select * from task_batches where batch_id = ?",
            (run_id,),
        ).fetchone()
        sources = conn.execute(
            "select * from sources where source_scope = 'research' order by platform"
        ).fetchall()
        tasks = conn.execute(
            "select * from tasks where task_id in "
            "(select task_id from task_batch_items where batch_id = ?) order by target_id",
            (run_id,),
        ).fetchall()

    assert batch["batch_type"] == "research"
    assert len(sources) == 2
    assert all(row["enabled"] == 1 and row["source_type"] == "research" for row in sources)
    assert {
        row["connector_id"] for row in sources
    } == {
        f"research:{run_id}:github",
        f"research:{run_id}:x",
    }
    assert len(tasks) == 2
    assert all(row["task_type"] == "fetch" for row in tasks)
    assert all(json.loads(row["artifacts_summary"])["research_batch_id"] == run_id for row in tasks)


def test_create_research_run_replays_same_body_and_rejects_changed_body(tmp_path) -> None:
    first_id, _ = create_research_run(
        tmp_path,
        payload=_payload(),
        idempotency_key="request-1",
    )
    replay_id, replayed = create_research_run(
        tmp_path,
        payload=_payload(),
        idempotency_key="request-1",
    )

    assert (replay_id, replayed) == (first_id, True)
    with pytest.raises(ResearchRequestError) as exc_info:
        create_research_run(
            tmp_path,
            payload={**_payload(), "name": "changed"},
            idempotency_key="request-1",
        )
    assert exc_info.value.code == "idempotency_conflict"


def test_repeated_research_batches_keep_distinct_observation_connectors(tmp_path) -> None:
    first_id, _ = create_research_run(
        tmp_path,
        payload=_payload(platforms=[
            {"platform": "github", "max_items": 10, "sort": "latest", "options": {}},
        ]),
        idempotency_key="request-1",
    )
    second_id, _ = create_research_run(
        tmp_path,
        payload=_payload(platforms=[
            {"platform": "github", "max_items": 10, "sort": "latest", "options": {}},
        ]),
        idempotency_key="request-2",
    )

    with connect_existing(resolve_sqlite_path(tmp_path)) as conn:
        rows = conn.execute(
            """
            select source_id, connector_id from sources
            where source_scope = 'research' and platform = 'github'
            order by source_id
            """
        ).fetchall()

    assert {row["source_id"] for row in rows} == {
        f"{first_id}_github",
        f"{second_id}_github",
    }
    assert len({row["connector_id"] for row in rows}) == 2


def test_invalid_selected_rss_source_rolls_back_whole_batch(tmp_path) -> None:
    migrate_database_path(resolve_sqlite_path(tmp_path))
    with pytest.raises(ResearchRequestError):
        create_research_run(
            tmp_path,
            payload=_payload(
                platforms=[
                    {
                        "platform": "rss",
                        "max_items": 20,
                        "sort": "latest",
                        "options": {"source_ids": ["missing"]},
                    }
                ]
            ),
            idempotency_key="rss-request",
        )

    with connect_existing(resolve_sqlite_path(tmp_path)) as conn:
        assert conn.execute(
            "select count(*) from task_batches where batch_type = 'research'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "select count(*) from sources where source_scope = 'research'"
        ).fetchone()[0] == 0


def test_rss_research_snapshots_only_selected_maintained_feed(tmp_path) -> None:
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_selected",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Selected feed",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_selected",
            "raw_metadata": {
                "feed_provider": "generic",
                "feed_content_mode": "feed_preferred",
            },
        }
    )

    run_id, _ = create_research_run(
        tmp_path,
        payload=_payload(platforms=[
            {
                "platform": "rss",
                "max_items": 20,
                "sort": "latest",
                "options": {"source_ids": ["rss_selected"]},
            },
        ]),
        idempotency_key="rss-request",
    )

    research_source = SourceRepository(tmp_path).get_source(f"{run_id}_rss")
    rss_sources = research_source["raw_metadata"]["research"]["rss_sources"]
    assert rss_sources == [
        {
            "source_id": "rss_selected",
            "feed_url": "https://example.com/feed.xml",
            "feed_provider": "generic",
            "feed_content_mode": "feed_preferred",
        }
    ]


def test_research_fetch_commit_never_writes_cursor_or_schedule_state(tmp_path) -> None:
    run_id, _ = create_research_run(
        tmp_path,
        payload=_payload(platforms=[
            {"platform": "github", "max_items": 10, "sort": "latest", "options": {}},
        ]),
        idempotency_key="request-1",
    )
    source_id = f"{run_id}_github"
    source = SourceRepository(tmp_path).get_source(source_id)
    raw_item = RawItem(
        raw_item_id="repo-1",
        connector_id=source["connector_id"],
        source_platform="github",
        source_type="repository",
        source_url="https://github.com/openai/example",
        captured_at="2026-07-31T00:00:00Z",
        raw_text="Example",
        raw_metadata={
            "source_id": source_id,
            "source_scope": "research",
            "research_batch_id": run_id,
        },
    )

    commit_fetch_result(
        tmp_path,
        source=source,
        run_id="research-run-1",
        raw_items=[raw_item],
        next_cursor="must-not-persist",
        chain_id=f"research-chain-{run_id}",
        chain_root_task_id="research-root",
        committed_last_run_at="2026-07-31T01:00:00Z",
        committed_next_run_at="2026-08-01T01:00:00Z",
        research_fetch=True,
    )

    stored = SourceRepository(tmp_path).get_source(source_id)
    assert SourceCursorRepository(tmp_path).get(source_id) is None
    assert stored["last_run_at"] is None
    assert stored["next_run_at"] is None
