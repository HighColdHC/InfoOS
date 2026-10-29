"""Canonical, read-only information-card projection for Obsidian clients."""
from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
import json
from backend.domain.card_titles import display_title
from backend.domain.timestamps import normalize_timestamp
from pathlib import Path
import re
from collections.abc import Sequence
from typing import Literal, TypedDict, cast

from backend.domain.advertising import AdvertisingProjection, advertising_projection
from backend.infrastructure.storage.cards_types import CardRow
from backend.infrastructure.storage.card_content_blocks_types import CardContentBlockRow
from backend.infrastructure.storage.plugin_card_queries_types import (
    PluginAssetRow, PluginCandidateRow, PluginCatalogRow,
)
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from backend.domain.content_blocks import USER_FACING_CONTENT_BLOCK_KINDS
from backend.infrastructure.storage.paths import resolve_data_dir
from backend.infrastructure.storage.plugin_card_queries import plugin_card_queries
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
)


LiteralCountKey = Literal["image_count", "video_count", "audio_count"]


class AssetSummary(TypedDict):
    image_count: int
    video_count: int
    audio_count: int
    total_size_bytes: int
    poster_asset_id: str | None


class PluginCatalogItem(AdvertisingProjection):
    card_id: str
    card_type: str
    version: int
    content_hash: str | None
    title: str
    source_id: str | None
    source_platform: str
    published_at: str | None
    updated_at: str
    status: str
    completeness_status: str
    excerpt: str
    source_url: str | None
    asset_summary: AssetSummary


class PluginCardPage(TypedDict):
    items: list[PluginCatalogItem]
    next_page_token: str | None


class PluginCardDetailItem(PluginCatalogItem):
    source_type: str
    source_author: str | None
    captured_at: str | None
    first_seen_at: str | None
    missing_reasons: object
    processor_version: str | None
    raw_connector_id: str | None
    raw_item_id: str | None
    source_run_id: str | None


class PluginBlockItem(TypedDict):
    block_id: str
    position: int
    layer: str
    kind: str
    original_kind: str | None
    status: str
    body: str
    segments: object
    source_url: str | None
    provenance: str


class PluginAssetItem(TypedDict):
    asset_id: str
    kind: str
    mime_type: str | None
    size_bytes: int | None
    content_hash: str | None
    status: str
    url: str


class ResolvedPluginAsset(TypedDict):
    asset_id: str
    kind: str
    mime_type: str | None
    size_bytes: int
    content_hash: str | None
    status: str
    url: str
    card_id: str
    path: Path


class PluginCardDetail(TypedDict):
    schema: str
    card: PluginCardDetailItem
    blocks: list[PluginBlockItem]
    assets: list[PluginAssetItem]


class DeliveredAsset(TypedDict):
    asset_id: str
    card_id: str
    asset_kind: str
    mime_type: str | None
    size_bytes: int
    content_hash: str | None
    status: str
    cleanup_state: str
    local_path: str | None


CARD_SCHEMA = "infoos.information-card.v1"
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_INTERNAL_BLOCK_KINDS = {"enrichment_gap", "llm_context", "media_download_gap"}
_DELIVERABLE_ASSET_STATES = {"done", "downloaded", "enriched", "preserved", "ready", "understood"}
_CREDENTIAL_QUERY_KEYS = {
    "token",
    "access_token",
    "auth",
    "authorization",
    "api_key",
    "apikey",
    "key",
    "secret",
    "signature",
    "sig",
    "x-amz-signature",
    "x-goog-signature",
}


class InvalidPageTokenError(ValueError):
    def __init__(self):
        super().__init__("invalid page token")


def read_plugin_card_page(
    base_dir: Path | str,
    *,
    page_size: int = 100,
    page_token: str | None = None,
    query: str | None = None,
    platform: str | None = None,
    completeness: str | None = None,
    media_kind: str | None = None,
    source_ids: list[str] | tuple[str, ...] | None = None,
) -> PluginCardPage:
    base_path = Path(base_dir)
    cursor = _decode_cursor(page_token) if page_token else None
    bounded_size = max(1, min(int(page_size), 200))
    normalized_media_kind = (media_kind or "").strip().casefold()
    selected_source_ids = (
        sorted({source_id.strip() for source_id in source_ids})
        if source_ids is not None else None
    )
    selected: list[PluginCandidateRow] = []
    summaries: dict[str, AssetSummary] = {}
    with plugin_card_queries(base_path) as reader:
        while len(selected) < bounded_size + 1:
            remaining = bounded_size + 1 - len(selected)
            candidates = reader.candidates(
                after=cursor, limit=remaining,
                query=(query or "").strip().casefold(),
                platform=(platform or "").strip().casefold(),
                completeness=(completeness or "").strip().casefold(),
                media_kind=normalized_media_kind, source_ids=selected_source_ids,
                asset_states=_DELIVERABLE_ASSET_STATES,
            )
            if not candidates:
                break
            assets = reader.assets_for_cards([card["card_id"] for card in candidates])
            for card in candidates:
                card_id = card["card_id"]
                summary = _summarize_assets(_deliverable_assets(base_path, assets[card_id]))
                if normalized_media_kind and summary[cast("LiteralCountKey", f"{normalized_media_kind}_count")] == 0:
                    continue
                summaries[card_id] = summary
                selected.append(card)
            cursor = _catalog_key(candidates[-1])
            if len(candidates) < remaining:
                break
        has_more = len(selected) > bounded_size
        page = selected[:bounded_size]
        bodies = reader.content_for_cards([card["card_id"] for card in page])
        items = []
        for card in page:
            catalog_row: PluginCatalogRow = {**card, **bodies[card["card_id"]]}
            items.append(_catalog_item(catalog_row, summaries[card["card_id"]]))
    next_page_token = _encode_cursor(_catalog_key(page[-1])) if has_more and page else None
    return {"items": items, "next_page_token": next_page_token}


def read_plugin_card_detail(base_dir: Path | str, card_id: str) -> PluginCardDetail | None:
    base_path = Path(base_dir)
    card = CardRepository(base_path).get_card(card_id)
    if card is None or not _is_stable_card(card):
        return None
    blocks = [
        _block_item(block)
        for block in CardContentBlockRepository(base_path).list_for_card(card_id)
        if block["block_kind"] not in _INTERNAL_BLOCK_KINDS
    ]
    has_readable_block = any(block["body"].strip() or block["segments"] for block in blocks)
    if not has_readable_block and card["content"].strip():
        blocks.insert(0,
            {
                "block_id": f"fallback-{card_id}-content",
                "position": min((block["position"] for block in blocks), default=1) - 1,
                "layer": "original",
                "kind": "card_content",
                "original_kind": None,
                "status": "preserved",
                "body": card["content"],
                "segments": [],
                "source_url": _safe_public_url(card["source_url"]),
                "provenance": "card content fallback",
            }
        )
    assets = _deliverable_assets(base_path, CardAssetRepository(base_path).list_for_card(card_id))
    detail_card: PluginCardDetailItem = {
        **_catalog_item(card, _summarize_assets(assets)),
        "source_type": card["source_type"],
        "source_url": _safe_public_url(card["source_url"]),
        "source_author": card["source_author"],
        "captured_at": card["first_seen_at"],
        "first_seen_at": card["first_seen_at"],
        "completeness_status": card["completeness_status"],
        "missing_reasons": card["missing_reasons"],
        "processor_version": card["processor_version"],
        "raw_connector_id": card["raw_connector_id"],
        "raw_item_id": card["raw_item_id"],
        "source_run_id": card["source_run_id"],
    }
    return {
        "schema": CARD_SCHEMA,
        "card": detail_card,
        "blocks": blocks,
        "assets": [_asset_item(asset) for asset in assets],
    }


def resolve_plugin_asset(base_dir: Path | str, asset_id: str) -> ResolvedPluginAsset | None:
    base_path = Path(base_dir)
    asset = CardAssetRepository(base_path).get_asset(asset_id)
    if asset is None:
        return None
    card = CardRepository(base_path).get_card(asset["card_id"])
    if card is None or not _is_stable_card(card):
        return None
    return _resolved_asset(base_path, asset)


def _catalog_item(card: CardRow | PluginCatalogRow, asset_summary: AssetSummary) -> PluginCatalogItem:
    content = (card["summary"] or card["content"]).strip()
    return {
        "card_id": card["card_id"],
        **advertising_projection(card["raw_payload"]),
        "card_type": card["card_type"],
        "version": card["version"],
        "content_hash": card["content_hash"],
        "title": display_title(card["source_platform"], card["title"], card["content"]),
        "source_id": card["source_id"] or None,
        "source_platform": card["source_platform"],
        "published_at": normalize_timestamp(card["published_at"]),
        "updated_at": card["updated_at"],
        "status": card["status"],
        "completeness_status": card["completeness_status"],
        "excerpt": content[:280],
        "source_url": _safe_public_url(card["source_url"]),
        "asset_summary": asset_summary,
    }


def _deliverable_assets(base_path: Path, assets: Sequence[PluginAssetRow]) -> list[DeliveredAsset]:
    result: list[DeliveredAsset] = []
    for asset in assets:
        resolved = _resolved_asset(base_path, asset)
        if resolved is not None:
            delivered: DeliveredAsset = {**asset, "size_bytes": resolved["size_bytes"]}
            result.append(delivered)
    return result


def _summarize_assets(assets: list[DeliveredAsset]) -> AssetSummary:
    counts = {"image": 0, "video": 0, "audio": 0}
    total = 0
    poster_asset_id = None
    for asset in assets:
        kind = asset["asset_kind"].strip().lower()
        if kind in counts:
            counts[kind] += 1
        total += asset["size_bytes"]
        if poster_asset_id is None and kind == "image":
            poster_asset_id = asset["asset_id"]
    return {
        "image_count": counts["image"],
        "video_count": counts["video"],
        "audio_count": counts["audio"],
        "total_size_bytes": total,
        "poster_asset_id": poster_asset_id,
    }


def _block_item(block: CardContentBlockRow) -> PluginBlockItem:
    original_kind = block["block_kind"]
    known = original_kind in USER_FACING_CONTENT_BLOCK_KINDS
    return {
        "block_id": block["block_id"],
        "position": block["position"],
        "layer": block["layer"],
        "kind": original_kind if known else "unknown",
        "original_kind": None if known else original_kind,
        "status": block["status"],
        "body": block["body"],
        "segments": block["segments"],
        "source_url": _safe_public_url(block["source_url"]),
        "provenance": block["provenance"],
    }


def _asset_item(asset: PluginAssetRow | DeliveredAsset) -> PluginAssetItem:
    return {
        "asset_id": asset["asset_id"],
        "kind": asset["asset_kind"],
        "mime_type": asset["mime_type"],
        "size_bytes": asset["size_bytes"],
        "content_hash": asset["content_hash"],
        "status": asset["status"],
        "url": "/api/plugin/v1/assets/" + quote(asset["asset_id"], safe=""),
    }


def _resolved_asset(base_dir: Path, asset: PluginAssetRow) -> ResolvedPluginAsset | None:
    if asset["status"] not in _DELIVERABLE_ASSET_STATES or asset["cleanup_state"] != "present":
        return None
    local_path = asset["local_path"]
    if not local_path:
        return None
    assets_root = (resolve_data_dir(base_dir) / "assets").resolve()
    target = (assets_root / local_path).resolve()
    try:
        target.relative_to(assets_root)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return {
        **_asset_item(asset),
        "card_id": asset["card_id"],
        "path": target,
        "size_bytes": target.stat().st_size,
    }


def _is_stable_card(card: CardRow) -> bool:
    return (
        card["card_type"] == "information"
        and card["status"] == "active"
        and card["version"] >= 1
        and bool((card["content_hash"] or "").strip())
    )


def _safe_public_url(value: str | None) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        query_keys = {
            key.casefold()
            for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
        }
    except (UnicodeError, ValueError):
        return None
    if query_keys & _CREDENTIAL_QUERY_KEYS:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _catalog_key(card: PluginCandidateRow) -> tuple[str, str]:
    return card["updated_at"], card["card_id"]


def _encode_cursor(value: tuple[str, str]) -> str:
    payload = json.dumps(
        {"updated_at": value[0], "card_id": value[1]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[str, str]:
    try:
        if not value or not _CURSOR_PATTERN.fullmatch(value):
            raise ValueError
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(urlsafe_b64decode(value + padding).decode("utf-8"))
        if set(decoded) != {"updated_at", "card_id"}:
            raise ValueError
        updated_at = decoded["updated_at"]
        card_id = decoded["card_id"]
        if not isinstance(updated_at, str) or not isinstance(card_id, str) or not card_id:
            raise ValueError
        return updated_at, card_id
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, BinasciiError) as exc:
        raise InvalidPageTokenError() from exc
