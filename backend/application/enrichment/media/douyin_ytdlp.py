"""Canonical yt-dlp media fallback for Douyin cards."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Any

from backend.application.enrichment.media.artifacts import create_media_blocks
from backend.application.enrichment.media.tooling import run_yt_dlp_download
from backend.domain.sources.douyin import parse_douyin_item_url
from backend.infrastructure.connectors.douyin.item import valid_douyin_metadata
from backend.infrastructure.runtime.environment import chrome_profile_dir
from backend.infrastructure.storage.paths import resolve_data_dir


def download_douyin_with_ytdlp(card: dict[str, Any], base_dir: Path) -> tuple[list[dict[str, Any]], str | None]:
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    identity = parse_douyin_item_url(card["source_url"])
    if identity is None:
        return [], "douyin_ytdlp_invalid_canonical_url"
    card_id = card["card_id"]
    assets_root = resolve_data_dir(base_dir) / "assets"
    output_dir = assets_root / card_id / "douyin"
    output_dir.mkdir(parents=True, exist_ok=True)
    job_dir = Path(tempfile.mkdtemp(prefix=".yt-dlp-", dir=output_dir))
    job_dir.chmod(0o700)
    result = run_yt_dlp_download(
        identity.canonical_url,
        job_dir,
        cookies_from_browser=f"chrome:{chrome_profile_dir()}",
    )
    if result.status != "downloaded":
        shutil.rmtree(job_dir, ignore_errors=True)
        return [], result.reason or "douyin_ytdlp_download_failed"
    info = result.metadata.get("info")
    if not isinstance(info, dict) or not valid_douyin_metadata(info, identity):
        shutil.rmtree(job_dir, ignore_errors=True)
        return [], "douyin_ytdlp_info_mismatch"
    if not any(file.kind == "video" for file in result.files):
        shutil.rmtree(job_dir, ignore_errors=True)
        return [], "douyin_ytdlp_no_video_file"

    files: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for media_file in result.files:
        digest = _sha256_file(media_file.path)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        target = output_dir / f"{digest[:16]}{media_file.path.suffix.lower()}"
        shutil.move(str(media_file.path), target)
        target.chmod(0o600)
        files.append(
            {
                "path": target,
                "relative_path": target.relative_to(assets_root).as_posix(),
                "kind": media_file.kind,
                "mime": media_file.mime,
                "size": media_file.size,
                "content_hash": digest,
            }
        )
    shutil.rmtree(job_dir, ignore_errors=True)
    blocks = create_media_blocks(
        card_id,
        identity.canonical_url,
        files,
        CardAssetRepository(base_dir),
        CardContentBlockRepository(base_dir),
        sub_kind="douyin",
        provenance_prefix="Douyin yt-dlp",
        asset_provenance={
            "strategy": "yt_dlp",
            "aweme_id": identity.aweme_id,
            "extractor": str(info.get("extractor_key") or info.get("extractor") or ""),
            "info_json_sha256": result.metadata.get("info_json_sha256"),
        },
    )
    return blocks, None


def existing_douyin_video_blocks(card_id: str, base_dir: Path) -> list[dict[str, Any]]:
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    assets_root = resolve_data_dir(base_dir) / "assets"
    assets = {
        asset["asset_id"]: asset
        for asset in CardAssetRepository(base_dir).list_for_card(card_id)
        if asset["asset_kind"] == "video" and asset["status"] in {"downloaded", "understood"}
    }
    valid_ids = {
        asset_id
        for asset_id, asset in assets.items()
        if _valid_local_asset(assets_root, asset["local_path"] or "")
    }
    return [
        block
        for block in CardContentBlockRepository(base_dir).list_for_card(card_id)
        if block["block_kind"] == "video"
        and isinstance(block["raw_payload"], dict)
        and str(block["raw_payload"].get("asset_id") or "") in valid_ids
    ]


def _valid_local_asset(assets_root: Path, local_path: str) -> bool:
    if not local_path:
        return False
    try:
        path = (assets_root / local_path).resolve()
        path.relative_to(assets_root.resolve())
    except (OSError, ValueError):
        return False
    return path.is_file() and path.stat().st_size > 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
