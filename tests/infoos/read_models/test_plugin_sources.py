from __future__ import annotations

import re

import pytest

from backend.infrastructure.storage.repositories import CardRepository, SourceRepository
from backend.read_models.plugin_sources import (
    InvalidSourcePageTokenError,
    read_plugin_source_page,
)

from tests.infoos.read_models.test_plugin_card_queries import catalog_seed, legacy_card_stats, legacy_card_page
from backend.read_models.plugin_sources import _stable_card_stats
from backend.read_models.plugin_cards import read_plugin_card_page



def _source(
    base_dir,
    source_id: str,
    *,
    enabled: bool = True,
    system_managed: bool = False,
) -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": source_id,
            "platform": "rss",
            "source_type": "feed",
            "identifier": f"https://example.test/{source_id}.xml",
            "display_name": f"Source {source_id}",
            "cadence_hours": 12,
            "enabled": enabled,
            "connector_status": "supported",
            "raw_metadata": {"system_managed": system_managed},
        }
    )


def _card(
    base_dir,
    card_id: str,
    source_id: str,
    *,
    updated_at: str,
    status: str = "active",
    version: int = 1,
    content_hash: str | None = None,
) -> None:
    CardRepository(base_dir).upsert_card(
        {
            "card_id": card_id,
            "card_type": "information",
            "version": version,
            "content_hash": content_hash if content_hash is not None else f"sha256:{'a' * 64}",
            "updated_at": updated_at,
            "status": status,
            "completeness_status": "complete",
            "missing_reasons": [],
            "source_id": source_id,
            "source_platform": "rss",
            "source_type": "article",
            "source_url": f"https://example.test/{card_id}",
            "title": card_id,
            "content": card_id,
            "captured_at": updated_at,
            "processed_at": updated_at,
        }
    )


def test_catalog_only_exposes_enabled_user_sources_with_stable_card_stats(tmp_path) -> None:
    _source(tmp_path, "a-source")
    _source(tmp_path, "b-source")
    _source(tmp_path, "disabled", enabled=False)
    _source(tmp_path, "system", system_managed=True)
    _card(tmp_path, "a-old", "a-source", updated_at="2026-07-30T01:00:00Z")
    _card(tmp_path, "a-new", "a-source", updated_at="2026-07-30T02:00:00Z")
    _card(
        tmp_path,
        "a-processing",
        "a-source",
        updated_at="2026-07-30T03:00:00Z",
        status="processing",
        version=0,
        content_hash="",
    )
    _card(
        tmp_path,
        "a-withdrawn",
        "a-source",
        updated_at="2026-07-30T04:00:00Z",
        status="withdrawn",
    )

    result = read_plugin_source_page(tmp_path)

    assert result == {
        "schema": "infoos.source-catalog.v1",
        "items": [
            {
                "source_id": "a-source",
                "display_name": "Source a-source",
                "platform": "rss",
                "source_type": "feed",
                "card_count": 2,
                "latest_card_updated_at": "2026-07-30T02:00:00Z",
            },
            {
                "source_id": "b-source",
                "display_name": "Source b-source",
                "platform": "rss",
                "source_type": "feed",
                "card_count": 0,
                "latest_card_updated_at": None,
            },
        ],
        "next_page_token": None,
    }
    assert "identifier" not in str(result)
    assert "raw_metadata" not in str(result)


def test_source_catalog_uses_opaque_cursor_and_rejects_invalid_tokens(tmp_path) -> None:
    _source(tmp_path, "a-source")
    _source(tmp_path, "b-source")

    first = read_plugin_source_page(tmp_path, page_size=1)
    second = read_plugin_source_page(
        tmp_path,
        page_size=1,
        page_token=first["next_page_token"],
    )

    assert re.fullmatch(r"[A-Za-z0-9_-]+", first["next_page_token"])
    assert [item["source_id"] for item in first["items"]] == ["a-source"]
    assert [item["source_id"] for item in second["items"]] == ["b-source"]
    assert second["next_page_token"] is None
    with pytest.raises(InvalidSourcePageTokenError, match="page token"):
        read_plugin_source_page(tmp_path, page_token="broken")




def test_sql_source_stats_match_head(catalog_seed):
    assert _stable_card_stats(catalog_seed) == legacy_card_stats(catalog_seed)
    assert _stable_card_stats(catalog_seed)['empty-time'] == (1, None)


def test_plugin_catalogs_never_load_all_cards(catalog_seed, monkeypatch):
    cards = legacy_card_page(catalog_seed, page_size=7)
    sources = read_plugin_source_page(catalog_seed)

    def forbidden(*args, **kwargs):
        raise AssertionError('plugin catalogs must not load the full card table')

    monkeypatch.setattr(CardRepository, 'list_cards', forbidden, raising=False)
    assert read_plugin_card_page(catalog_seed, page_size=7) == cards
    assert read_plugin_source_page(catalog_seed) == sources
