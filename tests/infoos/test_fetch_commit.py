"""Contracts for atomically committing a fetched source page."""

from __future__ import annotations

import sqlite3

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.input_versions import card_build_input_version
from backend.application.tasks.planner import raw_item_target_id, raw_item_task_id
from backend.application.tasks.types import TaskStatus, TaskType
from backend.domain.models.raw_item import RawItem
from backend.domain.raw_item_revisions import raw_content_hash
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.repositories import CardRepository, SourceCursorRepository, SourceRepository
from backend.infrastructure.storage.task_events import TaskEventRepository
from tests.infoos._factories import make_source_payload


def _source() -> dict[str, object]:
    return make_source_payload(
        source_id="source_fetch_commit",
        connector_id="connector_fetch_commit",
    )


def _raw_item(
    raw_item_id: str = "raw_fetch_commit_1",
    *,
    connector_id: str = "connector_fetch_commit",
    source_id: str = "source_fetch_commit",
    title: str = "Atomic fetch",
    raw_text: str = "Atomic fetch result",
) -> RawItem:
    return RawItem(
        raw_item_id=raw_item_id,
        connector_id=connector_id,
        source_platform="rss",
        source_type="article",
        source_url=f"https://example.com/{connector_id}/{raw_item_id}",
        captured_at="2026-07-10T08:00:00Z",
        raw_text=raw_text,
        raw_timestamps={"published_at": "2026-07-10T07:00:00Z"},
        raw_metadata={"source_id": source_id, "title": title},
    )


def _seed_source(tmp_path) -> dict[str, object]:
    source = _source()
    SourceRepository(tmp_path).upsert_source(source)
    return source


def _table_counts(tmp_path) -> dict[str, int]:
    db_path = tmp_path / "data" / "infoos.sqlite3"
    with sqlite3.connect(db_path) as conn:
        return {
            table: int(conn.execute(f"select count(*) from {table}").fetchone()[0])
            for table in ("raw_items", "source_cursors", "tasks")
        }


def _raw_item_input_version(raw_item: RawItem) -> str:
    return card_build_input_version({
        "raw_item_id": raw_item.raw_item_id,
        "source_url": raw_item.source_url,
        "raw_text": raw_item.raw_text,
        "published_at": raw_item.raw_timestamps.published_at if raw_item.raw_timestamps else None,
        "raw_metadata": raw_item.raw_metadata,
    })


def _commit(
    tmp_path,
    *,
    raw_items: list[RawItem],
    next_cursor: str = "cursor-next",
    source: dict[str, object] | None = None,
):
    from backend.application.workflows.fetch_commit import commit_fetch_result

    source = source or _source()
    SourceRepository(tmp_path).upsert_source(source)
    return commit_fetch_result(
        tmp_path,
        source=source,
        run_id="run_fetch_commit",
        raw_items=raw_items,
        next_cursor=next_cursor,
        chain_id="source_run_source_fetch_commit_20260710_080000",
        chain_root_task_id="fetch_source_fetch_commit_20260710_080000",
    )


def test_fetch_commit_persists_raw_cursor_and_tasks_together(tmp_path) -> None:
    raw_item = _raw_item()

    result = _commit(tmp_path, raw_items=[raw_item])

    assert result.raw_item_ids == [raw_item.raw_item_id]
    assert result.card_build_task_ids == [
        raw_item_task_id(
            TaskType.CARD_BUILD,
            raw_item.raw_item_id,
            connector_id=raw_item.connector_id,
            input_version=_raw_item_input_version(raw_item),
        )
    ]
    assert result.cursor_value == "cursor-next"
    assert _table_counts(tmp_path) == {"raw_items": 1, "source_cursors": 1, "tasks": 1}
    task = TaskRepository.for_base_dir(tmp_path).get(result.card_build_task_ids[0])
    assert task is not None
    assert task.task_type == TaskType.CARD_BUILD
    assert task.target_kind == "raw_item"
    assert task.target_id == raw_item_target_id(raw_item.connector_id, raw_item.raw_item_id)
    assert task.input_version == _raw_item_input_version(raw_item)
    assert task.artifacts_summary == {
        "chain_id": "source_run_source_fetch_commit_20260710_080000",
        "chain_root_task_id": "fetch_source_fetch_commit_20260710_080000",
        "run_id": "run_fetch_commit",
        "source_id": "source_fetch_commit",
    }
    assert SourceCursorRepository(tmp_path).get("source_fetch_commit")["cursor_value"] == "cursor-next"


def test_fetch_commit_rolls_back_all_rows_when_task_insert_fails(tmp_path, monkeypatch) -> None:
    from backend.application.workflows.fetch_commit import commit_fetch_result

    _seed_source(tmp_path)
    original_enqueue_many = TaskRepository.enqueue_many

    def fail_after_task_insert(self, tasks, *, conn):
        original_enqueue_many(self, tasks, conn=conn)
        raise RuntimeError("task insert failure")

    monkeypatch.setattr(TaskRepository, "enqueue_many", fail_after_task_insert)

    with pytest.raises(RuntimeError, match="task insert failure"):
        commit_fetch_result(
            tmp_path,
            source=_source(),
            run_id="run_fetch_commit",
            raw_items=[_raw_item()],
            next_cursor="cursor-next",
            chain_id="chain-1",
            chain_root_task_id="fetch-1",
        )

    assert _table_counts(tmp_path) == {"raw_items": 0, "source_cursors": 0, "tasks": 0}


def test_fetch_commit_rolls_back_cursor_when_raw_insert_fails(tmp_path, monkeypatch) -> None:
    from backend.application.workflows.fetch_commit import commit_fetch_result

    _seed_source(tmp_path)
    original_upsert = RawItemRepository.upsert_raw_items_with_changes

    def fail_after_raw_insert(self, items, *, run_id=None, conn=None):
        original_upsert(self, items, run_id=run_id, conn=conn)
        raise RuntimeError("raw insert failure")

    monkeypatch.setattr(RawItemRepository, "upsert_raw_items_with_changes", fail_after_raw_insert)

    with pytest.raises(RuntimeError, match="raw insert failure"):
        commit_fetch_result(
            tmp_path,
            source=_source(),
            run_id="run_fetch_commit",
            raw_items=[_raw_item()],
            next_cursor="cursor-next",
            chain_id="chain-1",
            chain_root_task_id="fetch-1",
        )

    assert _table_counts(tmp_path) == {"raw_items": 0, "source_cursors": 0, "tasks": 0}


def test_fetch_commit_rolls_back_acquisition_when_event_append_is_busy(tmp_path, monkeypatch) -> None:
    """An acquisition observation is part of the raw/cursor/task commit."""
    from backend.application.workflows.fetch_commit import commit_fetch_result

    _seed_source(tmp_path)

    def busy_event_append(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(TaskEventRepository, "append_to_connection", busy_event_append)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        commit_fetch_result(
            tmp_path,
            source=_source(),
            run_id="run_fetch_commit",
            raw_items=[_raw_item()],
            next_cursor="cursor-next",
            chain_id="chain-1",
            chain_root_task_id="fetch-1",
        )

    assert _table_counts(tmp_path) == {"raw_items": 0, "source_cursors": 0, "tasks": 0}
    assert TaskEventRepository(tmp_path).list_events() == []


def _card_build_rows(tmp_path) -> int:
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        return conn.execute("select count(*) from tasks where task_type = 'card_build'").fetchone()[0]


def test_fetch_commit_replay_does_not_duplicate_raw_items_or_tasks(tmp_path) -> None:
    raw_item = _raw_item()

    first = _commit(tmp_path, raw_items=[raw_item])
    second = _commit(tmp_path, raw_items=[raw_item])

    assert second.raw_item_ids == first.raw_item_ids
    assert second.card_build_task_ids == first.card_build_task_ids
    assert _table_counts(tmp_path) == {"raw_items": 1, "source_cursors": 1, "tasks": 1}


def test_fetch_commit_keeps_chain_id_on_card_build_tasks(tmp_path) -> None:
    raw_items = [_raw_item("raw_fetch_commit_1"), _raw_item("raw_fetch_commit_2")]

    result = _commit(tmp_path, raw_items=raw_items)

    tasks = [TaskRepository.for_base_dir(tmp_path).get(task_id) for task_id in result.card_build_task_ids]
    assert [task.artifacts_summary["chain_id"] for task in tasks if task is not None] == [
        "source_run_source_fetch_commit_20260710_080000",
        "source_run_source_fetch_commit_20260710_080000",
    ]
    assert [task.artifacts_summary["chain_root_task_id"] for task in tasks if task is not None] == [
        "fetch_source_fetch_commit_20260710_080000",
        "fetch_source_fetch_commit_20260710_080000",
    ]


def test_fetch_commit_namespaces_card_build_targets_by_connector(tmp_path) -> None:
    from backend.application.tasks.registry import handler_for

    first_source = _source()
    second_source = {
        **_source(),
        "source_id": "source_fetch_commit_second",
        "connector_id": "connector_fetch_commit_second",
        "identifier": "https://example.com/second.xml",
    }
    first_raw = _raw_item(
        "shared-raw-id",
        title="First connector title",
        raw_text="First connector text",
    )
    second_raw = _raw_item(
        "shared-raw-id",
        connector_id="connector_fetch_commit_second",
        source_id="source_fetch_commit_second",
        title="Second connector title",
        raw_text="Second connector text",
    )

    first = _commit(tmp_path, raw_items=[first_raw], source=first_source)
    second = _commit(tmp_path, raw_items=[second_raw], source=second_source)

    assert first.card_build_task_ids != second.card_build_task_ids
    first_task = TaskRepository.for_base_dir(tmp_path).get(first.card_build_task_ids[0])
    second_task = TaskRepository.for_base_dir(tmp_path).get(second.card_build_task_ids[0])
    assert first_task is not None
    assert second_task is not None
    assert first_task.target_id != second_task.target_id

    first_result = handler_for(TaskType.CARD_BUILD)(first_task, tmp_path)
    second_result = handler_for(TaskType.CARD_BUILD)(second_task, tmp_path)

    assert CardRepository(tmp_path).get_card(first_result["artifacts"]["card_id"])["title"] == "First connector title"
    assert CardRepository(tmp_path).get_card(second_result["artifacts"]["card_id"])["title"] == "Second connector title"


def test_fetch_commit_requeues_successful_card_build_when_raw_input_changes(tmp_path) -> None:
    original = _raw_item()
    first = _commit(tmp_path, raw_items=[original])
    repo = TaskRepository.for_base_dir(tmp_path)
    task_id = first.card_build_task_ids[0]
    repo.update_status(task_id, TaskStatus.SUCCESS)

    changed = _raw_item(raw_text="Changed source payload")
    replay = _commit(tmp_path, raw_items=[changed])
    task = repo.get(replay.card_build_task_ids[0])

    assert replay.card_build_task_ids != first.card_build_task_ids
    assert task is not None
    assert task.status == TaskStatus.PENDING
    assert task.attempt == 1
    assert task.input_version == _raw_item_input_version(changed)
    original_task = repo.get(first.card_build_task_ids[0])
    assert original_task is not None
    assert original_task.status == TaskStatus.SUCCESS


def test_fetch_commit_exact_replay_preserves_successful_card_build(tmp_path) -> None:
    raw_item = _raw_item()
    first = _commit(tmp_path, raw_items=[raw_item])
    repo = TaskRepository.for_base_dir(tmp_path)
    task_id = first.card_build_task_ids[0]
    repo.update_status(task_id, TaskStatus.SUCCESS)

    replay = _commit(tmp_path, raw_items=[raw_item])
    task = repo.get(task_id)

    assert replay.card_build_task_ids == first.card_build_task_ids
    assert _card_build_rows(tmp_path) == 1
    assert task is not None
    assert task.status == TaskStatus.SUCCESS


def test_fetch_commit_rejects_historical_render_and_counter_noise(tmp_path) -> None:
    original = _raw_item(raw_text="Version A")
    original.raw_metadata["metrics"] = {"likes": 1}
    first = _commit(tmp_path, raw_items=[original])
    changed = _raw_item(raw_text="Version B")
    changed.raw_metadata["metrics"] = {"likes": 2}
    second = _commit(tmp_path, raw_items=[changed])
    noisy = _raw_item(raw_text="Version A")
    noisy.raw_metadata["metrics"] = {"likes": 3}
    third = _commit(tmp_path, raw_items=[noisy])

    assert len(first.card_build_task_ids) == len(second.card_build_task_ids) == 1
    assert second.card_build_task_ids != first.card_build_task_ids
    assert _card_build_rows(tmp_path) == 2
    stored = RawItemRepository(tmp_path).get_for_connector(original.connector_id, original.raw_item_id)
    assert stored["raw_text"] == "Version B"
    assert stored["raw_payload"]["raw_metadata"]["metrics"]["likes"] == 3
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        assert conn.execute("select revision from raw_items").fetchone()[0] == 2
        assert conn.execute("select count(*) from raw_item_revisions").fetchone()[0] == 2


def test_fetch_commit_ignores_replacement_character_only(tmp_path) -> None:
    original = _raw_item(raw_text="abCde")
    _commit(tmp_path, raw_items=[original])
    damaged = _raw_item(raw_text="ab\ufffdde")
    _commit(tmp_path, raw_items=[damaged])
    assert _card_build_rows(tmp_path) == 1
    assert RawItemRepository(tmp_path).get_model(original.raw_item_id).raw_text == "abCde"


@pytest.mark.parametrize("new_text, expected_revision, expected_tasks", [
    ("Before migration", 1, 0),
    ("First post-migration observation", 2, 1),
])
def test_first_observation_of_legacy_null_hash_compares_with_baseline(
    tmp_path, new_text, expected_revision, expected_tasks,
) -> None:
    original = _raw_item(raw_text="Before migration")
    _commit(tmp_path, raw_items=[original])
    db = tmp_path / "data" / "infoos.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("delete from raw_item_revisions")
        conn.execute("update raw_items set content_hash = null, revision = 0")
    changed = _raw_item(raw_text=new_text)
    _commit(tmp_path, raw_items=[changed])
    assert _card_build_rows(tmp_path) == 1 + expected_tasks
    assert RawItemRepository(tmp_path).get_model(original.raw_item_id).raw_text == new_text
    with sqlite3.connect(db) as conn:
        assert conn.execute("select revision from raw_items").fetchone()[0] == expected_revision
        assert conn.execute("select count(*) from raw_item_revisions").fetchone()[0] == expected_revision


def test_comments_body_and_nonnumeric_score_are_content_not_counters(tmp_path) -> None:
    original = _raw_item()
    original.raw_metadata.update({"comments": [{"text": "first"}], "score": "editorial"})
    _commit(tmp_path, raw_items=[original])
    changed = _raw_item()
    changed.raw_metadata.update({"comments": [{"text": "second"}], "score": "editorial"})
    _commit(tmp_path, raw_items=[changed])
    assert _card_build_rows(tmp_path) == 2
    assert RawItemRepository(tmp_path).get_model(original.raw_item_id).raw_metadata["comments"] == [{"text": "second"}]


def test_numeric_score_and_comment_count_remain_volatile() -> None:
    first = _raw_item()
    first.raw_metadata.update({"comments": 1, "score": -2})
    second = _raw_item()
    second.raw_metadata.update({"comments": 5, "score": 3})
    assert raw_content_hash(first) == raw_content_hash(second)


def test_raw_content_hash_uses_media_set_and_excludes_observation_fields() -> None:
    first = _raw_item()
    first.raw_metadata["media_urls"] = ["https://example.com/a", "https://example.com/b"]
    first.raw_metadata["metrics"] = {"likes": 1}
    second = _raw_item()
    second.captured_at = "2026-09-28T10:00:00Z"
    second.raw_metadata["media_urls"] = ["https://example.com/b", "https://example.com/a"]
    second.raw_metadata["metrics"] = {"likes": 99}
    assert raw_content_hash(first) == raw_content_hash(second)
    second.raw_metadata["media_urls"] = ["https://example.com/c"]
    assert raw_content_hash(first) != raw_content_hash(second)
