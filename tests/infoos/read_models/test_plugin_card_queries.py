"""HEAD reference implementations and SQL read-path equivalence tests."""
from pathlib import Path
import shutil
from typing import Any

import pytest

from backend.domain.card_titles import display_title
from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.storage.repositories import CardRepository, CardAssetRepository, CardContentBlockRepository, SourceRepository
from backend.read_models.plugin_cards import (
    _decode_cursor, _encode_cursor, _catalog_key, _is_stable_card,
    _resolved_asset, _safe_public_url, _asset_item, _block_item, _INTERNAL_BLOCK_KINDS, CARD_SCHEMA,
)

def _catalog_item(base_path: Path, card: dict[str, Any]) -> dict[str, Any]:
    content = str(card.get("summary") or card.get("content") or "").strip()
    return {
        "card_id": str(card["card_id"]),
        "card_type": str(card.get("card_type") or "information"),
        "version": int(card.get("version") or 0),
        "content_hash": str(card.get("content_hash") or ""),
        "title": display_title(card.get("source_platform"), str(card.get("title") or ""), card.get("content")),
        "source_id": str(card["source_id"]) if card.get("source_id") else None,
        "source_platform": str(card.get("source_platform") or "unknown"),
        "published_at": normalize_timestamp(card.get("published_at")),
        "updated_at": str(card.get("updated_at") or ""),
        "status": str(card.get("status") or "processing"),
        "completeness_status": str(card.get("completeness_status") or "processing"),
        "excerpt": content[:280],
        "source_url": _safe_public_url(card.get("source_url")),
        "asset_summary": _asset_summary(base_path, str(card["card_id"])),
    }


def _asset_summary(base_path: Path, card_id: str) -> dict[str, Any]:
    counts = {"image": 0, "video": 0, "audio": 0}
    total = 0
    poster_asset_id = None
    for asset in CardAssetRepository(base_path).list_for_card(card_id):
        resolved = _resolved_asset(base_path, asset)
        if resolved is None:
            continue
        kind = str(asset.get("asset_kind") or "").strip().lower()
        if kind in counts:
            counts[kind] += 1
        total += int(resolved["size_bytes"])
        if poster_asset_id is None and kind == "image":
            poster_asset_id = str(asset["asset_id"])
    return {
        "image_count": counts["image"],
        "video_count": counts["video"],
        "audio_count": counts["audio"],
        "total_size_bytes": total,
        "poster_asset_id": poster_asset_id,
    }


def _active_fixture_cards(base_path: Path) -> list[dict[str, Any]]:
    repo = CardRepository(base_path)
    with repo.connect_existing() as conn:
        rows = conn.execute("select card_id from cards where status = 'active'").fetchall()
        return [repo.get_card(row["card_id"], connection=conn) for row in rows]


def legacy_card_page(
    base_dir: Path | str,
    *,
    page_size: int = 100,
    page_token: str | None = None,
    query: str | None = None,
    platform: str | None = None,
    completeness: str | None = None,
    media_kind: str | None = None,
    source_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    base_path = Path(base_dir)
    cursor = _decode_cursor(page_token) if page_token else None
    cards = [
        card
        for card in _active_fixture_cards(base_path)
        if _is_stable_card(card)
    ]
    normalized_query = str(query or "").strip().casefold()
    normalized_platform = str(platform or "").strip().casefold()
    normalized_completeness = str(completeness or "").strip().casefold()
    normalized_media_kind = str(media_kind or "").strip().casefold()
    if normalized_query:
        cards = [
            card
            for card in cards
            if normalized_query
            in " ".join(
                str(card.get(key) or "")
                for key in ("title", "summary", "content", "source_url")
            ).casefold()
        ]
    if normalized_platform:
        cards = [
            card
            for card in cards
            if str(card.get("source_platform") or "").casefold() == normalized_platform
        ]
    if normalized_completeness:
        cards = [
            card
            for card in cards
            if str(card.get("completeness_status") or "").casefold()
            == normalized_completeness
        ]
    if normalized_media_kind:
        cards = [
            card
            for card in cards
            if _asset_summary(base_path, str(card["card_id"]))[
                f"{normalized_media_kind}_count"
            ]
            > 0
        ]
    if source_ids is not None:
        selected_source_ids = {str(source_id).strip() for source_id in source_ids}
        cards = [
            card
            for card in cards
            if card.get("source_id") is not None
            and str(card["source_id"]) in selected_source_ids
        ]
    cards.sort(key=_catalog_key)
    if cursor is not None:
        cards = [card for card in cards if _catalog_key(card) > cursor]
    bounded_size = max(1, min(int(page_size), 200))
    selected = cards[: bounded_size + 1]
    has_more = len(selected) > bounded_size
    page = selected[:bounded_size]
    next_page_token = _encode_cursor(_catalog_key(page[-1])) if has_more and page else None
    return {
        "items": [_catalog_item(base_path, card) for card in page],
        "next_page_token": next_page_token,
    }


def _is_stable_information_card(card: dict[str, Any]) -> bool:
    return (
        str(card.get("card_type") or "information") == "information"
        and card.get("status") == "active"
        and int(card.get("version") or 0) >= 1
        and bool(str(card.get("content_hash") or "").strip())
    )


def legacy_card_stats(base_path: Path) -> dict[str, tuple[int, str | None]]:
    stats: dict[str, tuple[int, str | None]] = {}
    for card in _active_fixture_cards(base_path):
        source_id = str(card.get("source_id") or "")
        if not source_id or not _is_stable_information_card(card):
            continue
        count, latest = stats.get(source_id, (0, None))
        updated_at = str(card.get("updated_at") or "") or None
        stats[source_id] = (
            count + 1,
            max(value for value in (latest, updated_at) if value is not None)
            if latest or updated_at
            else None,
        )
    return stats



def legacy_card_detail(base_dir: Path | str, card_id: str) -> dict[str, Any] | None:
    base_path = Path(base_dir)
    card = CardRepository(base_path).get_card(card_id)
    if card is None or not _is_stable_card(card):
        return None
    blocks = [
        _block_item(block)
        for block in CardContentBlockRepository(base_path).list_for_card(card_id)
        if str(block.get("block_kind") or "") not in _INTERNAL_BLOCK_KINDS
    ]
    has_readable_block = any(
        str(block.get("body") or "").strip() or block.get("segments")
        for block in blocks
    )
    if not has_readable_block and str(card.get("content") or "").strip():
        blocks.insert(0,
            {
                "block_id": f"fallback-{card_id}-content",
                "position": min((block["position"] for block in blocks), default=1) - 1,
                "layer": "original",
                "kind": "card_content",
                "original_kind": None,
                "status": "preserved",
                "body": str(card["content"]),
                "segments": [],
                "source_url": _safe_public_url(card.get("source_url")),
                "provenance": "card content fallback",
            }
        )
    assets = []
    for asset in CardAssetRepository(base_path).list_for_card(card_id):
        resolved = _resolved_asset(base_path, asset)
        if resolved is not None:
            assets.append(_asset_item(asset) | {"size_bytes": resolved["size_bytes"]})
    return {
        "schema": CARD_SCHEMA,
        "card": _catalog_item(base_path, card)
        | {
            "source_type": str(card.get("source_type") or "unknown"),
            "source_url": _safe_public_url(card.get("source_url")),
            "source_author": card.get("source_author"),
            "captured_at": card.get("first_seen_at"),
            "first_seen_at": card.get("first_seen_at"),
            "completeness_status": str(card.get("completeness_status") or "processing"),
            "missing_reasons": list(card.get("missing_reasons") or []),
            "processor_version": card.get("processor_version"),
            "raw_connector_id": card.get("raw_connector_id"),
            "raw_item_id": card.get("raw_item_id"),
            "source_run_id": card.get("source_run_id"),
        },
        "blocks": blocks,
        "assets": assets,
    }



@pytest.fixture(scope='session')
def _catalog_template(tmp_path_factory, runtime_storage_template):
    tmp_path = tmp_path_factory.mktemp('catalog-template')
    shutil.copytree(runtime_storage_template, tmp_path, dirs_exist_ok=True)
    for source_id in ('source-a', 'source-b', 'empty-time', ''):
        SourceRepository(tmp_path).upsert_source({
            'source_id': source_id, 'platform': 'rss', 'source_type': 'feed',
            'identifier': f'https://example.test/{source_id}', 'display_name': source_id,
            'cadence_hours': 12, 'enabled': True, 'connector_status': 'supported',
        })
    cards = CardRepository(tmp_path)
    assets = CardAssetRepository(tmp_path)
    for index in range(54):
        card_id = f'card-{index:02}'
        timestamp = f'2026-10-01T{index // 6:02}:00:00Z'
        cards.upsert_card({
            'card_id': card_id, 'card_type': 'information', 'version': 1,
            'content_hash': f' sha256:{index} \t', 'updated_at': timestamp, 'status': 'active',
            'completeness_status': 'complete' if index % 2 else 'partial',
            'source_id': ('source-a', 'source-b', None, '')[index % 4],
            'source_platform': ('rss', 'RSS', 'x', 'X', 'Straße')[index % 5],
            'source_type': 'article', 'source_url': f'https://example.test/{card_id}?lang=zh#part',
            'title': 'Hi.' if index % 5 in (2, 3) else f'Title Straße {index} tail',
            'content': 'Hi. Useful body sentence!\n' + ('正文 %_ ß ' * 50),
            'summary': 'head summary' if index % 3 else '',
            'published_at': '2026-10-01T00:00:00+08:00',
            'captured_at': timestamp, 'processed_at': timestamp, 'ignored': index % 2,
            'raw_payload': {'large': 'excluded from SQL catalog'},
        })
        for kind in ('image', 'video', 'audio', 'document'):
            local_path = f'{card_id}/{kind}.bin'
            target = tmp_path / 'data' / 'assets' / local_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if index >= 10:
                target.write_bytes(f'{index}-{kind}'.encode())
            assets.upsert_asset({
                'asset_id': f'{card_id}-{kind}', 'card_id': card_id,
                'asset_kind': f' {kind.upper()} ' if index % 2 else kind,
                'source_url': f'https://cdn.test/{kind}', 'local_path': local_path,
                'mime_type': f'{kind}/test', 'size_bytes': 99999,
                'status': 'downloaded' if index % 3 else 'preserved',
                'cleanup_state': 'deleted' if index in (10, 11) else 'present',
                'created_at': timestamp,
            })
        if index == 12:
            assets.upsert_asset({
                'asset_id': 'first-poster', 'card_id': card_id, 'asset_kind': 'image',
                'source_url': 'https://cdn.test/poster', 'local_path': local_path,
                'status': 'ready', 'created_at': '2020-01-01T00:00:00Z',
            })
    with cards.connect() as conn:
        # Existing databases can lack the fresh-schema CHECK constraints.
        conn.execute('pragma ignore_check_constraints = on')
        for index, assignments in (
            (46, "status = 'processing'"), (47, "status = 'withdrawn'"),
            (48, 'version = 0'), (49, "content_hash = ' \t\n\u2003\u00a0'"),
            (50, 'content_hash = null'), (51, "card_type = 'event'"),
            (52, "source_id = 'empty-time', updated_at = ''"),
            (53, "completeness_status = 'COMPLETE'"),
        ):
            conn.execute(f'update cards set {assignments} where card_id = ?', (f'card-{index:02}',))
        conn.execute("update card_assets set cleanup_state = '' where card_id = 'card-13'")
        conn.execute("update card_assets set status = 'pending' where card_id = 'card-14'")
    return tmp_path


@pytest.fixture
def catalog_seed(_catalog_template, tmp_path):
    # Each test gets an independent database and assets; only construction is shared.
    shutil.copytree(_catalog_template, tmp_path, dirs_exist_ok=True)
    return tmp_path


def all_pages(reader, base_dir, **filters):
    pages, token = [], None
    for _ in range(100):
        result = reader(base_dir, page_token=token, **filters)
        pages.append(result)
        token = result['next_page_token']
        if token is None:
            return pages
    pytest.fail('catalog did not terminate')
