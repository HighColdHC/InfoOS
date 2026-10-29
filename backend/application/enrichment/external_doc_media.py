from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from backend.application.enrichment.media.artifacts import looks_like_media_file
from backend.infrastructure.external_docs.feishu_browser import FeishuMediaBlob
from backend.infrastructure.storage.paths import resolve_data_dir
from backend.infrastructure.storage.raw_media import RawMediaStore
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}


def persist_authenticated_external_media(
    card_id: str,
    document_url: str,
    blobs: list[FeishuMediaBlob] | list[dict[str, Any]],
    base_dir: Path | str,
) -> list[dict[str, Any]]:
    if not blobs:
        return []
    if isinstance(blobs[0], dict):
        blobs = RawMediaStore(resolve_data_dir(base_dir)).load(blobs)
    assets_root = resolve_data_dir(base_dir) / "assets"
    safe_card_dir = re.sub(r"[^A-Za-z0-9_.-]", "_", card_id)[:160] or "card"
    target_dir = assets_root / safe_card_dir / "external-doc"
    target_dir.mkdir(parents=True, exist_ok=True)
    asset_repo = CardAssetRepository(base_dir)
    block_repo = CardContentBlockRepository(base_dir)
    existing_positions = {
        block["block_id"]: block["position"]
        for block in block_repo.list_for_card(card_id)
    }
    next_position = max(existing_positions.values(), default=-1) + 1
    persisted: list[dict[str, Any]] = []
    for blob in blobs:
        extension = _MIME_EXTENSIONS.get(blob.mime_type)
        if extension is None:
            continue
        digest = hashlib.sha256(blob.content).hexdigest()
        path = target_dir / f"{digest}{extension}"
        if not path.exists():
            temporary = path.with_suffix(f"{path.suffix}.tmp")
            temporary.write_bytes(blob.content)
            if not looks_like_media_file(temporary, blob.kind):
                temporary.unlink(missing_ok=True)
                continue
            os.replace(temporary, path)
        elif not looks_like_media_file(path, blob.kind):
            continue
        relative_path = str(path.relative_to(assets_root))
        asset_id = f"asset_{card_id}_external_{digest[:16]}"
        block_id = f"block_{card_id}_{blob.kind}_external_{digest[:16]}"
        asset_repo.upsert_asset(
            {
                "asset_id": asset_id,
                "card_id": card_id,
                "asset_kind": blob.kind,
                "source_url": document_url,
                "remote_url": blob.url,
                "local_path": relative_path,
                "mime_type": blob.mime_type,
                "size_bytes": len(blob.content),
                "content_hash": f"sha256:{digest}",
                "preservation_class": "cached",
                "status": "downloaded",
                "cleanup_state": "present",
                "raw_payload": {
                    "strategy": "feishu_authenticated_browser",
                    "document_url": document_url,
                },
            }
        )
        position = existing_positions.get(block_id, next_position)
        if block_id not in existing_positions:
            existing_positions[block_id] = position
            next_position += 1
        block = {
            "block_id": block_id,
            "card_id": card_id,
            "position": position,
            "layer": "media",
            "block_kind": blob.kind,
            "status": "downloaded",
            "body": relative_path,
            "source_url": blob.url,
            "provenance": "Feishu authenticated document media",
            "raw_payload": {
                "asset_id": asset_id,
                "strategy": "feishu_authenticated_browser",
                "document_url": document_url,
            },
        }
        block_repo.upsert_block(block)
        persisted.append(block)
    return persisted
