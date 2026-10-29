"""Pure rules for stable, externally deliverable information-card versions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_RUNTIME_KEYS = frozenset(
    {
        "attempt",
        "captured_at",
        "cleanup_state",
        "created_at",
        "local_path",
        "log",
        "logs",
        "processed_at",
        "source_run_id",
        "status",
        "task_id",
        "updated_at",
    }
)


def canonical_information_card_payload(
    card: Mapping[str, Any],
    blocks: Iterable[Mapping[str, Any]],
    assets: Iterable[Mapping[str, Any]],
    *,
    completeness_status: str,
    missing_reasons: Sequence[Any],
) -> dict[str, Any]:
    """Return the stable facts that define one deliverable card version.

    Operational trace, attempts, timestamps, absolute paths, temporary statuses,
    and processor metadata are intentionally absent.  The result is suitable for
    deterministic JSON serialization and hashing.
    """

    source_url = _normalize_url(card.get("source_url"))
    if source_url:
        source_identity: dict[str, Any] = {"source_url": source_url}
    else:
        source_identity = {
            "connector_id": _text(card.get("raw_connector_id")),
            "raw_item_id": _text(card.get("raw_item_id")),
        }

    canonical_blocks = [_readable_block(block) for block in blocks]
    canonical_blocks.sort(
        key=lambda block: (
            int(block.pop("_sort_position")),
            str(block.pop("_sort_id")),
        )
    )

    canonical_assets = [_stable_asset(asset) for asset in assets]
    canonical_assets.sort(
        key=lambda asset: (
            str(asset.pop("_sort_kind")),
            str(asset.pop("_sort_id")),
        )
    )

    normalized_missing = [_canonical_json_value(reason) for reason in missing_reasons]
    normalized_missing.sort(key=_canonical_json)

    return {
        "card": {
            "card_type": _text(card.get("card_type") or "information"),
            "source_id": _nullable_text(card.get("source_id")),
            "source_platform": _text(card.get("source_platform")),
            "source_type": _text(card.get("source_type")),
            "source_author": _nullable_text(card.get("source_author")),
            "title": _text(card.get("title")),
            "content": _text(card.get("content")),
            "summary": _text(card.get("summary")),
            "published_at": _nullable_text(card.get("published_at")),
        },
        "source_identity": source_identity,
        "content_blocks": canonical_blocks,
        "assets": canonical_assets,
        "completeness_status": _text(completeness_status),
        "missing_reasons": normalized_missing,
    }


def information_card_content_hash(
    card: Mapping[str, Any],
    blocks: Iterable[Mapping[str, Any]],
    assets: Iterable[Mapping[str, Any]],
    *,
    completeness_status: str,
    missing_reasons: Sequence[Any],
) -> str:
    """Hash the canonical deliverable facts of an information card."""

    payload = canonical_information_card_payload(
        card,
        blocks,
        assets,
        completeness_status=completeness_status,
        missing_reasons=missing_reasons,
    )
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _readable_block(block: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "_sort_position": int(block.get("position") or 0),
        "_sort_id": _text(block.get("block_id")),
        "position": int(block.get("position") or 0),
        "layer": _text(block.get("layer")),
        "block_kind": _text(block.get("block_kind")),
        "body": _text(block.get("body")),
        "segments": _canonical_json_value(block.get("segments") or []),
        "source_url": _nullable_normalized_url(block.get("source_url")),
        "provenance": _text(block.get("provenance")),
    }


def _stable_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    kind = _text(asset.get("asset_kind"))
    return {
        "_sort_kind": kind,
        "_sort_id": _text(asset.get("asset_id")),
        "asset_kind": kind,
        "source_url": _normalize_url(asset.get("source_url")),
        "remote_url": _nullable_normalized_url(asset.get("remote_url")),
        "mime_type": _nullable_text(asset.get("mime_type")),
        "size_bytes": _nullable_int(asset.get("size_bytes")),
        "content_hash": _nullable_text(asset.get("content_hash")),
        "preservation_class": _text(asset.get("preservation_class") or "cached"),
    }


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(nested)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in _RUNTIME_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _normalize_url(value: Any) -> str:
    raw = _text(value).strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return raw
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    port = parsed.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((scheme, netloc, parsed.path or "/", query, ""))


def _nullable_normalized_url(value: Any) -> str | None:
    normalized = _normalize_url(value)
    return normalized or None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _nullable_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _nullable_int(value: Any) -> int | None:
    return None if value is None else int(value)
