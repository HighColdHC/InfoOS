"""Generate missing or stale derived media thumbnails."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from backend.infrastructure.media.thumbnails import ThumbnailError, render_thumbnail, thumbnail_relative_path
from backend.infrastructure.storage.card_asset_thumbnails import CardAssetThumbnailRepository
from backend.infrastructure.storage.card_asset_thumbnails_types import ThumbnailSourceRow
from backend.infrastructure.storage.paths import resolve_data_dir


def ensure_card_thumbnails(base_dir: Path, card_id: str) -> None:
    """Generate missing or stale thumbnails for one card's deliverable image/video assets."""
    repo = CardAssetThumbnailRepository(base_dir)
    for asset in repo.list_pending_for_card(card_id):
        generate_asset_thumbnail(base_dir, asset, repo=repo)


def generate_asset_thumbnail(base_dir: Path, asset: ThumbnailSourceRow, *,
                             repo: CardAssetThumbnailRepository) -> Literal["ready", "failed"]:
    assets_root = (resolve_data_dir(base_dir) / "assets").resolve()
    source = (assets_root / asset["local_path"]).resolve()
    try:
        source.relative_to(assets_root)
    except ValueError:
        repo.mark_failed(asset["asset_id"], error_code="source_outside_assets", source_content_hash=asset["content_hash"])
        return "failed"
    if not source.is_file():
        repo.mark_failed(asset["asset_id"], error_code="source_missing", source_content_hash=asset["content_hash"])
        return "failed"
    relative = thumbnail_relative_path(asset["asset_id"])
    target = assets_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        rendered = render_thumbnail(source, temporary, kind=asset["media_kind"])
    except ThumbnailError as exc:
        temporary.unlink(missing_ok=True)
        repo.mark_failed(asset["asset_id"], error_code=exc.code, source_content_hash=asset["content_hash"])
        return "failed"
    os.replace(temporary, target)
    repo.mark_ready(asset["asset_id"], local_path=relative, width=rendered.width, height=rendered.height,
                    size_bytes=rendered.size_bytes, source_content_hash=asset["content_hash"])
    return "ready"
