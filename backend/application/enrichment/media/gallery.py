from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.enrichment.media.artifacts import create_media_blocks, upsert_media_gap_block
from backend.application.enrichment.media.tooling import run_gallery_dl_download
from backend.infrastructure.runtime.environment import chrome_profile_dir
from backend.infrastructure.storage.paths import resolve_data_dir


def download_gallery_card_media(card_id: str, source_url: str, base_dir: Path, *, platform: str) -> list[dict[str, Any]]:
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    block_repo = CardContentBlockRepository(base_dir)
    assets_root = resolve_data_dir(base_dir) / "assets"
    output_dir = assets_root / card_id / "gallery"
    result = run_gallery_dl_download(
        source_url,
        output_dir,
        cookies_from_browser=f"chrome:{chrome_profile_dir()}",
    )
    if result.status != "downloaded":
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url=source_url,
            reason=result.reason or "gallery_download_failed",
            details={"strategy": "gallery_dl", "platform": platform, "status": result.status, "stderr": result.stderr[-500:]},
        )
        return []

    files = [
        {
            "path": file.path,
            "relative_path": file.relative_path,
            "kind": file.kind,
            "mime": file.mime,
            "size": file.size,
        }
        for file in result.files
    ]
    asset_repo = CardAssetRepository(base_dir)
    blocks = create_media_blocks(
        card_id,
        source_url,
        files,
        asset_repo,
        block_repo,
        sub_kind="gallery",
        provenance_prefix=f"{platform} gallery",
    )
    _mark_gallery_assets(asset_repo, card_id, platform)
    return blocks


def _mark_gallery_assets(asset_repo: Any, card_id: str, platform: str) -> None:
    for asset in asset_repo.list_for_card(card_id):
        raw_payload = asset.get("raw_payload") if isinstance(asset.get("raw_payload"), dict) else {}
        if raw_payload.get("sub_kind") != "gallery":
            continue
        updated = dict(asset)
        updated["raw_payload"] = {**raw_payload, "strategy": "gallery_dl", "platform": platform}
        asset_repo.upsert_asset(updated)
