from __future__ import annotations

import re

import pytest

from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
    SourceRepository,
)
from backend.read_models.plugin_cards import (
    InvalidPageTokenError,
    read_plugin_card_detail,
    read_plugin_card_page,
    resolve_plugin_asset,
)

from tests.infoos.read_models.test_plugin_card_queries import (
    all_pages, catalog_seed, legacy_card_detail, legacy_card_page,
)


def _card(
    base_dir,
    card_id: str,
    *,
    updated_at: str,
    status: str = "active",
    version: int = 1,
    content_hash: str | None = None,
    source_id: str | None = None,
    source_url: str | None = None,
) -> None:
    CardRepository(base_dir).upsert_card(
        {
            "card_id": card_id,
            "card_type": "information",
            "version": version,
            "content_hash": content_hash if content_hash is not None else f"sha256:{card_id[0] * 64}",
            "updated_at": updated_at,
            "status": status,
            "completeness_status": "complete",
            "missing_reasons": [],
            "processor_version": "collector-v1",
            "raw_connector_id": "connector-1",
            "raw_item_id": f"raw-{card_id}",
            "source_run_id": f"run-{card_id}",
            "source_id": source_id,
            "source_platform": "rss",
            "source_type": "article",
            "source_url": source_url if source_url is not None else f"https://example.test/{card_id}",
            "source_author": "Author",
            "title": f"Title {card_id}",
            "content": f"Body {card_id}",
            "published_at": "2026-07-16T00:00:00Z",
            "captured_at": "2026-07-16T00:00:00Z",
            "processed_at": "2026-07-16T00:00:00Z",
        }
    )


def _source(base_dir, source_id: str) -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": source_id,
            "platform": "rss",
            "source_type": "feed",
            "identifier": f"https://example.test/{source_id}.xml",
            "display_name": source_id,
            "cadence_hours": 12,
            "enabled": True,
            "connector_status": "supported",
        }
    )


def test_catalog_uses_stable_ascending_cursor_and_only_returns_finalized_cards(tmp_path) -> None:
    _card(tmp_path, "a-card", updated_at="2026-07-16T01:00:00Z")
    _card(tmp_path, "b-card", updated_at="2026-07-16T01:00:00Z")
    _card(tmp_path, "c-card", updated_at="2026-07-16T02:00:00Z")
    _card(tmp_path, "processing", updated_at="2026-07-16T00:00:00Z", status="processing", version=0, content_hash="")
    _card(tmp_path, "withdrawn", updated_at="2026-07-16T03:00:00Z", status="withdrawn")

    first = read_plugin_card_page(tmp_path, page_size=2)
    repeated = read_plugin_card_page(tmp_path, page_size=2)
    second = read_plugin_card_page(tmp_path, page_size=2, page_token=first["next_page_token"])

    assert [item["card_id"] for item in first["items"]] == ["a-card", "b-card"]
    assert first == repeated
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first["next_page_token"])
    assert [item["card_id"] for item in second["items"]] == ["c-card"]
    assert second["next_page_token"] is None
    assert set(first["items"][0]) == {
        "card_id",
        "card_type",
        "version",
        "content_hash",
        "title",
        "source_id",
        "source_platform",
        "published_at",
        "updated_at",
        "status",
        "completeness_status",
        "excerpt",
        "source_url",
        "asset_summary",
    }


def test_invalid_page_token_is_rejected_without_persisting_cursor_state(tmp_path) -> None:
    _card(tmp_path, "a-card", updated_at="2026-07-16T01:00:00Z")

    with pytest.raises(InvalidPageTokenError, match="page token"):
        read_plugin_card_page(tmp_path, page_size=10, page_token="not-valid-json")

    with CardRepository(tmp_path).connect_existing() as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    assert not any("cursor" in table and "source" not in table for table in tables)
    assert not any("ack" in table or "sync" in table for table in tables)


def test_detail_uses_fixed_schema_and_maps_unknown_block_kind_safely(tmp_path) -> None:
    _card(tmp_path, "d-card", updated_at="2026-07-16T03:00:00Z")
    blocks = CardContentBlockRepository(tmp_path)
    blocks.upsert_block(
        {
            "block_id": "known",
            "card_id": "d-card",
            "position": 1,
            "layer": "original",
            "block_kind": "card_content",
            "status": "preserved",
            "body": "Known body",
        }
    )
    blocks.upsert_block(
        {
            "block_id": "future",
            "card_id": "d-card",
            "position": 2,
            "layer": "derived",
            "block_kind": "future_executable_widget",
            "status": "preserved",
            "body": "Future body stays readable",
            "raw_payload": {"command": "must-not-be-exported"},
        }
    )

    detail = read_plugin_card_detail(tmp_path, "d-card")

    assert detail is not None
    assert detail["schema"] == "infoos.information-card.v1"
    assert detail["card"]["version"] == 1
    assert detail["card"]["content_hash"].startswith("sha256:")
    assert detail["blocks"] == [
        {
            "block_id": "known",
            "position": 1,
            "layer": "original",
            "kind": "card_content",
            "original_kind": None,
            "status": "preserved",
            "body": "Known body",
            "segments": [],
            "source_url": None,
            "provenance": "",
        },
        {
            "block_id": "future",
            "position": 2,
            "layer": "derived",
            "kind": "unknown",
            "original_kind": "future_executable_widget",
            "status": "preserved",
            "body": "Future body stays readable",
            "segments": [],
            "source_url": None,
            "provenance": "",
        },
    ]
    assert "raw_payload" not in str(detail)
    assert read_plugin_card_detail(tmp_path, "missing") is None


def test_detail_preserves_capture_text_when_only_comment_block_is_blocked(tmp_path) -> None:
    _card(tmp_path, "phone-capture", updated_at="2026-09-17T02:00:10Z")
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "blocked-comments",
            "card_id": "phone-capture",
            "position": 0,
            "layer": "context",
            "block_kind": "platform_comments",
            "status": "blocked",
            "body": "",
        }
    )

    detail = read_plugin_card_detail(tmp_path, "phone-capture")

    assert detail is not None
    assert detail["blocks"][0]["body"] == "Body phone-capture"
    assert detail["blocks"][0]["kind"] == "card_content"
    assert detail["blocks"][1]["block_id"] == "blocked-comments"
    assert detail["blocks"][1]["status"] == "blocked"
    assert detail["blocks"][0]["position"] < detail["blocks"][1]["position"]


def test_catalog_filters_by_repeated_source_ids_and_preserves_null_source_without_filter(
    tmp_path,
) -> None:
    _source(tmp_path, "source-a")
    _source(tmp_path, "source-b")
    _card(tmp_path, "a-card", updated_at="2026-07-16T01:00:00Z", source_id="source-a")
    _card(tmp_path, "b-card", updated_at="2026-07-16T02:00:00Z", source_id="source-b")
    _card(tmp_path, "null-card", updated_at="2026-07-16T03:00:00Z")

    unfiltered = read_plugin_card_page(tmp_path)
    filtered = read_plugin_card_page(
        tmp_path,
        source_ids=["source-a", "source-b"],
    )
    unknown = read_plugin_card_page(tmp_path, source_ids=["unknown"])

    assert [item["card_id"] for item in unfiltered["items"]] == [
        "a-card",
        "b-card",
        "null-card",
    ]
    assert [item["card_id"] for item in filtered["items"]] == ["a-card", "b-card"]
    assert unknown["items"] == []
    assert filtered["items"][0]["source_id"] == "source-a"
    assert unfiltered["items"][-1]["source_id"] is None


def test_public_source_urls_strip_fragments_and_reject_credentials(tmp_path) -> None:
    _card(
        tmp_path,
        "safe-card",
        updated_at="2026-07-16T01:00:00Z",
        source_url="https://example.test/post?lang=zh#section",
    )
    _card(
        tmp_path,
        "secret-card",
        updated_at="2026-07-16T02:00:00Z",
        source_url="https://example.test/post?lang=zh&access_token=secret",
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "secret-block",
            "card_id": "safe-card",
            "position": 1,
            "layer": "original",
            "block_kind": "card_content",
            "status": "preserved",
            "body": "body",
            "source_url": "https://user@example.test/private",
        }
    )

    catalog = read_plugin_card_page(tmp_path)
    detail = read_plugin_card_detail(tmp_path, "safe-card")

    assert catalog["items"][0]["source_url"] == "https://example.test/post?lang=zh"
    assert catalog["items"][1]["source_url"] is None
    assert detail is not None
    assert detail["card"]["source_url"] == "https://example.test/post?lang=zh"
    assert detail["blocks"][0]["source_url"] is None


def test_asset_resolution_requires_active_owner_present_state_and_managed_file(tmp_path) -> None:
    _card(tmp_path, "asset-card", updated_at="2026-07-16T03:00:00Z")
    assets_root = tmp_path / "data" / "assets"
    target = assets_root / "asset-card" / "video.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"0123456789")
    repo = CardAssetRepository(tmp_path)
    repo.upsert_asset(
        {
            "asset_id": "asset-ok",
            "card_id": "asset-card",
            "asset_kind": "video",
            "source_url": "https://cdn.test/video",
            "local_path": "asset-card/video.bin",
            "mime_type": "video/mp4",
            "size_bytes": 10,
            "status": "downloaded",
        }
    )
    repo.upsert_asset(
        {
            "asset_id": "asset-outside",
            "card_id": "asset-card",
            "asset_kind": "document",
            "source_url": "https://cdn.test/outside",
            "local_path": "../secret.txt",
            "status": "downloaded",
        }
    )

    resolved = resolve_plugin_asset(tmp_path, "asset-ok")

    assert resolved is not None
    assert resolved["path"] == target.resolve()
    assert resolved["size_bytes"] == 10
    assert resolve_plugin_asset(tmp_path, "asset-outside") is None
    assert resolve_plugin_asset(tmp_path, "missing") is None



@pytest.mark.parametrize('page_size', [1, 7, 200])
def test_sql_catalog_matches_head_for_all_pages_and_filters(catalog_seed, page_size):
    filters = [
        {}, {'query': 'STRASSE'}, {'query': '正文 %_ SS'}, {'query': 'tail head'},
        {'query': 'summary'}, {'query': 'example.test'}, {'query': 'no match'},
        {'platform': ' RSS '}, {'platform': 'strasse'}, {'platform': 'X'},
        {'completeness': ' COMPLETE '}, {'completeness': 'partial'},
        {'source_ids': [' source-a ', 'source-b', 'source-a']},
        {'source_ids': []}, {'source_ids': ['']}, {'source_ids': ['unknown']},
        {'media_kind': 'image'}, {'media_kind': ' VIDEO '}, {'media_kind': 'audio'},
        {'query': '正文', 'platform': 'X', 'media_kind': 'image'},
        {'source_ids': ['source-a'], 'completeness': 'partial', 'media_kind': 'audio'},
        {'query': 'strasse', 'completeness': 'complete', 'platform': 'rss'},
    ]
    for selected in filters:
        expected = all_pages(legacy_card_page, catalog_seed, page_size=page_size, **selected)
        actual = all_pages(read_plugin_card_page, catalog_seed, page_size=page_size, **selected)
        assert actual == expected, selected
        for page in expected:
            if page['next_page_token']:
                assert read_plugin_card_page(
                    catalog_seed, page_size=page_size, page_token=page['next_page_token'], **selected,
                ) == legacy_card_page(
                    catalog_seed, page_size=page_size, page_token=page['next_page_token'], **selected,
                ), selected
    items = [item for page in all_pages(read_plugin_card_page, catalog_seed, page_size=page_size)
             for item in page['items']]
    repaired = next(item for item in items if item['card_id'] == 'card-02')
    assert repaired['title'] == 'Useful body sentence!'


def test_media_filter_refills_after_missing_files(catalog_seed):
    page = read_plugin_card_page(catalog_seed, page_size=7, media_kind='image')
    assert len(page['items']) == 7
    assert page['next_page_token'] is not None
    assert page == legacy_card_page(catalog_seed, page_size=7, media_kind='image')
    assert not any(item['card_id'] in {f'card-{i:02}' for i in range(12)} for item in page['items'])


def test_detail_reads_and_resolves_assets_once(catalog_seed, monkeypatch):
    import backend.read_models.plugin_cards as projection
    expected = legacy_card_detail(catalog_seed, 'card-12')
    reads, resolutions = [], []
    original_read = CardAssetRepository.list_for_card
    original_resolve = projection._resolved_asset

    def read(self, card_id, **kwargs):
        reads.append(card_id)
        return original_read(self, card_id, **kwargs)

    def resolve(base_dir, asset):
        resolutions.append(asset['asset_id'])
        return original_resolve(base_dir, asset)

    monkeypatch.setattr(CardAssetRepository, 'list_for_card', read)
    monkeypatch.setattr(projection, '_resolved_asset', resolve)
    assert read_plugin_card_detail(catalog_seed, 'card-12') == expected
    assert reads == ['card-12']
    assert len(resolutions) == len(set(resolutions)) == 5


@pytest.mark.parametrize('page_size', [0, 201])
def test_sql_catalog_preserves_page_size_bounds(catalog_seed, page_size):
    assert read_plugin_card_page(catalog_seed, page_size=page_size) == legacy_card_page(
        catalog_seed, page_size=page_size)


def test_sql_reads_only_bounded_metadata_bodies_and_assets(catalog_seed, monkeypatch):
    from contextlib import contextmanager
    import backend.infrastructure.storage.plugin_card_queries as storage
    statements = []
    original = storage.connect_readonly

    @contextmanager
    def traced(path):
        with original(path) as conn:
            assert conn.execute('pragma query_only').fetchone()[0] == 1
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(storage, 'connect_readonly', traced)
    page = read_plugin_card_page(catalog_seed, page_size=7)
    selects = [sql for sql in statements if sql.startswith('select')]
    assert len(selects) == 3
    metadata, assets, bodies = selects
    assert 'limit 8' in metadata
    assert not any(field in metadata for field in ('c.content,', 'c.summary', 'raw_payload'))
    assert 'raw_items' not in metadata
    assert all(item['card_id'] in bodies for item in page['items'])
    assert 'card-06' not in bodies  # Lookahead body is never fetched.
    assert 'card_id in (' in assets
    assert 'order by card_id, created_at, asset_id' in assets


def test_sql_in_queries_split_at_500(catalog_seed):
    from backend.infrastructure.storage.plugin_card_queries import plugin_card_queries
    statements = []
    with plugin_card_queries(catalog_seed) as reader:
        reader.connection.set_trace_callback(statements.append)
        ids = [f'missing-{i}' for i in range(1000)] + ['card-01', 'card-02']
        assert set(reader.content_for_cards(ids)) == {'card-01', 'card-02'}
        assets = reader.assets_for_cards(ids)
        assert len(assets['card-01']) == 4
    assert len(statements) == 6
    sources = ['source-a', 'source-b'] + [f'source-az{i}' for i in range(550)]
    assert read_plugin_card_page(catalog_seed, source_ids=sources, page_size=7) == legacy_card_page(
        catalog_seed, source_ids=sources, page_size=7)
