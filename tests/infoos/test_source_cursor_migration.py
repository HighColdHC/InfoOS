"""Runtime source cursor contract: SQLite is the only cursor source."""

from __future__ import annotations

import json

from backend.application.workflows.fetch import fetch
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import SourceRepository
from tests.infoos._factories import make_supported_source_payload


def _seed_source(tmp_path, *, source_id: str = "source_rss", connector_id: str = "rss_cursor") -> None:
    SourceRepository(tmp_path).upsert_source(
        make_supported_source_payload(
            source_id=source_id,
            connector_id=connector_id,
        )
    )


def _write_legacy_cursor(tmp_path, connector_id: str, cursor_value: str) -> None:
    state_dir = tmp_path / "data" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{connector_id}.json").write_text(
        json.dumps({"last_seen_id": cursor_value}),
        encoding="utf-8",
    )


def test_runtime_cursor_read_does_not_fall_back_to_json(tmp_path, monkeypatch) -> None:
    _seed_source(tmp_path)
    _write_legacy_cursor(tmp_path, "rss_cursor", "legacy-item")
    observed_cursors: list[str | None] = []

    class FakeConnector:
        connector_id = "rss_cursor"

        def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
            observed_cursors.append(since_cursor)
            return FetchResult(items=[], next_cursor="next-item")

    # ``fetch`` delegates durable acquisition to this module. Patch its
    # imported connector boundary rather than the legacy fetch-module symbol,
    # which is order-dependent once acquisition has already been imported.
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda _base_dir, _source: FakeConnector(),
    )

    fetch(tmp_path)

    assert observed_cursors == [None]
