"""SQLite-backed source cursor storage contracts."""

from __future__ import annotations

from backend.infrastructure.storage.repositories import SourceRepository
from tests.infoos._factories import make_source_payload


def _seed_source(tmp_path, *, source_id: str = "source_rss", connector_id: str = "rss_cursor") -> None:
    SourceRepository(tmp_path).upsert_source(
        make_source_payload(
            source_id=source_id,
            connector_id=connector_id,
        )
    )


def test_source_cursor_round_trip_in_sqlite(tmp_path) -> None:
    from backend.infrastructure.storage.repositories import SourceCursorRepository

    _seed_source(tmp_path)
    repository = SourceCursorRepository(tmp_path)

    repository.upsert(
        source_id="source_rss",
        connector_id="rss_cursor",
        cursor_value="item-42",
        last_run_id="run-42",
        updated_at="2026-07-10T00:00:00Z",
    )

    assert repository.get("source_rss") == {
        "source_id": "source_rss",
        "connector_id": "rss_cursor",
        "cursor_value": "item-42",
        "last_run_id": "run-42",
        "updated_at": "2026-07-10T00:00:00Z",
    }


def test_source_cursor_upsert_is_idempotent(tmp_path) -> None:
    from backend.infrastructure.storage.repositories import SourceCursorRepository

    _seed_source(tmp_path)
    repository = SourceCursorRepository(tmp_path)
    payload = {
        "source_id": "source_rss",
        "connector_id": "rss_cursor",
        "cursor_value": "item-42",
        "last_run_id": "run-42",
        "updated_at": "2026-07-10T00:00:00Z",
    }

    repository.upsert(**payload)
    repository.upsert(**payload)

    with repository.connect() as conn:
        count = conn.execute("select count(*) from source_cursors").fetchone()[0]
    assert count == 1
    assert repository.get("source_rss") == payload
