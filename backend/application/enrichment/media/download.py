from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from backend.application.enrichment.media.candidates import collect_media_candidates
from backend.application.enrichment.media.x_attribution import (
    _attribute_opencli_files,
    _cross_card_duplicate,
    _persist_x_opencli_remote_urls,
)
from backend.application.enrichment.media.artifacts import (
    create_media_blocks as _create_media_blocks,
    download_direct_media_url as _download_direct_media_url,
    get_existing_asset_ids as _get_existing_asset_ids,
    looks_like_media_file as _looks_like_media_file,
    next_media_block_position as _next_generic_block_position,
    upsert_media_gap_block as _upsert_media_gap_block,
)
from backend.application.enrichment.media.douyin import download_douyin_card_media
from backend.application.enrichment.media.gallery import download_gallery_card_media
from backend.application.enrichment.media.xiaohongshu import download_xiaohongshu_card_media
from backend.application.enrichment.media.x_session import run_x_media_download_lifecycle
from backend.application.enrichment.media.x_downloader import run_x_yt_dlp_fallback
from backend.application.enrichment.media.x_urls import (
    X_MEDIA_HOSTS,
    parse_canonical_x_status_url,
)
from backend.application.enrichment.media.youtube import download_youtube_card_media
from backend.infrastructure.opencli.commands import pin_adapter_lifecycle
from backend.infrastructure.opencli.runtime import resolve_opencli_executable
from backend.infrastructure.storage.paths import resolve_data_dir

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}


def download_card_media(card_id: str, base_dir: Path) -> list[dict[str, Any]]:
    """Download direct media URLs for a card.

    X still uses the dedicated OpenCLI downloader. Other platforms only
    download direct image/video URLs already present in the card or its
    content-block payloads.
    """
    from backend.infrastructure.storage.repositories import CardRepository

    card = CardRepository(base_dir).get_card(card_id)
    if not card:
        return []
    source_url = card["source_url"]
    platform = card["source_platform"]
    if platform == "youtube":
        from backend.application.enrichment.youtube_blocks import has_usable_youtube_transcript
        from backend.infrastructure.storage.repositories import CardContentBlockRepository

        if has_usable_youtube_transcript(CardContentBlockRepository(base_dir).list_for_card(card_id)):
            return []
        return download_youtube_card_media(card_id, source_url, base_dir)
    candidates = collect_media_candidates(card, base_dir)
    if platform == "x":
        if candidates:
            direct_blocks = download_generic_card_media(card, base_dir, record_gap=False)
            if direct_blocks:
                return direct_blocks
        return download_x_card_media(card_id, source_url, base_dir)
    if platform == "xiaohongshu":
        return download_xiaohongshu_card_media(card_id, source_url, base_dir)
    if platform == "douyin":
        return download_douyin_card_media(card, base_dir)
    direct_blocks = download_generic_card_media(card, base_dir, record_gap=platform not in {"reddit"})
    if direct_blocks:
        return direct_blocks
    if platform == "reddit" and "/gallery/" in source_url:
        return download_gallery_card_media(card_id, source_url, base_dir, platform="reddit")
    return direct_blocks


def download_generic_card_media(
    card: dict[str, Any],
    base_dir: Path,
    *,
    record_gap: bool = True,
) -> list[dict[str, Any]]:
    from backend.infrastructure.storage.repositories import (
        CardAssetRepository,
        CardContentBlockRepository,
    )

    candidates = collect_media_candidates(card, base_dir)
    if not candidates:
        return []
    asset_repo = CardAssetRepository(base_dir)
    block_repo = CardContentBlockRepository(base_dir)
    assets_root = resolve_data_dir(base_dir) / "assets"
    assets_dir = assets_root / card["card_id"] / "generic"
    assets_dir.mkdir(parents=True, exist_ok=True)

    blocks: list[dict[str, Any]] = []
    existing_asset_ids = _get_existing_asset_ids(asset_repo, card["card_id"])
    for index, candidate in enumerate(candidates, start=1):
        url = candidate.url
        is_x = card["source_platform"] == "x"
        strategy = "direct_candidate"
        downloaded = _download_direct_media_url(
            url,
            assets_dir=assets_dir,
            index=index,
            allowed_hosts=X_MEDIA_HOSTS if is_x else None,
        )
        if not downloaded:
            continue
        asset_id = f"asset_{card['card_id']}_{downloaded['content_hash'][:16]}"
        candidate_provenance = _candidate_provenance(candidate.metadata) if is_x else {
            "source": candidate.source,
        }
        candidate_source_url = candidate_provenance.get("relation_source_url") or card["source_url"] or url
        if asset_id not in existing_asset_ids:
            asset_repo.upsert_asset({
                "asset_id": asset_id,
                "card_id": card["card_id"],
                "asset_kind": downloaded["kind"],
                "source_url": candidate_source_url,
                "remote_url": url,
                "local_path": str(downloaded["path"].relative_to(assets_root)),
                "mime_type": downloaded["mime_type"],
                "size_bytes": downloaded["size_bytes"],
                "content_hash": f"sha256:{downloaded['content_hash']}",
                "preservation_class": "cached",
                "status": "downloaded",
                "cleanup_state": "present",
                "raw_payload": {
                    "strategy": strategy,
                    **candidate_provenance,
                },
            })
        block = {
            "block_id": f"block_{card['card_id']}_{downloaded['kind']}_generic_{downloaded['content_hash'][:16]}",
            "card_id": card["card_id"],
            "position": _next_generic_block_position(block_repo, card["card_id"]),
            "layer": "media",
            "block_kind": downloaded["kind"],
            "status": "downloaded",
            "body": str(downloaded["path"].relative_to(assets_root)),
            "source_url": url,
            "provenance": f"Generic direct {downloaded['kind']} URL",
            "raw_payload": {
                "asset_id": asset_id,
                "strategy": strategy,
                **candidate_provenance,
            },
        }
        block_repo.upsert_block(block)
        blocks.append(block)
    if record_gap and not blocks:
        _upsert_media_gap_block(
            block_repo,
            card_id=card["card_id"],
            source_url=card["source_url"],
            reason="direct_media_download_failed",
            details={
                "strategy": "direct_candidate",
                "candidate_count": len(candidates),
            },
        )
    return blocks


def _candidate_provenance(metadata: dict[str, Any]) -> dict[str, Any]:
    provenance = {"tweet_id": metadata["tweet_id"]}
    if metadata.get("relation_depth") != 1:
        return provenance
    for key in (
        "relation_role",
        "relation_depth",
        "relation_block_kind",
        "relation_source_url",
    ):
        provenance[key] = metadata[key]
    return provenance


def download_x_card_media(
    card_id: str,
    tweet_url: str,
    base_dir: Path,
    *,
    sub_kind: str | None = None,
) -> list[dict[str, Any]]:
    """Download X images/videos for a card and store as card_assets.

    Returns list of created content block dicts (image/video blocks).
    """
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    status = parse_canonical_x_status_url(tweet_url)
    if status is None:
        block_repo = CardContentBlockRepository(base_dir)
        _upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url=tweet_url,
            reason="x_media_download_invalid_status_url",
        )
        return []
    tweet_url = status.url
    tweet_id = status.tweet_id

    assets_root = resolve_data_dir(base_dir) / "assets"
    assets_dir = assets_root / card_id
    assets_dir.mkdir(parents=True, exist_ok=True)

    asset_repo = CardAssetRepository(base_dir)
    block_repo = CardContentBlockRepository(base_dir)

    existing_blocks = _existing_verified_x_blocks(asset_repo, block_repo, assets_root, card_id, tweet_id)
    if existing_blocks:
        return existing_blocks

    lifecycle = run_x_media_download_lifecycle(
        tweet_url=tweet_url,
        staging_root=assets_dir,
        command_runner=lambda profile_alias, attempt_dir: subprocess.run(
            [resolve_opencli_executable("opencli"), *pin_adapter_lifecycle([
                "twitter", "download",
                "--tweet-url", tweet_url,
                "--limit", "1",
                "--output", str(attempt_dir),
                "--format", "json",
            ], profile_alias=profile_alias)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        ),
    )
    result = lifecycle.command_result
    opencli_reason = lifecycle.failure_reason
    attempt_dir = lifecycle.attempt_dir
    try:
        if result is not None and result.returncode == 0 and attempt_dir is not None:
            stdout = result.stdout
            json_start = stdout.find("[")
            if json_start >= 0:
                try:
                    entries = json.loads(stdout[json_start:])
                except json.JSONDecodeError:
                    opencli_reason = "x_media_download_invalid_json"
                else:
                    staged = _list_asset_files(attempt_dir)
                    attributed, rejected = _attribute_opencli_files(entries, staged, tweet_id)
                    duplicate_rejections: list[dict[str, Any]] = []
                    accepted = []
                    for item in attributed:
                        duplicate = _cross_card_duplicate(base_dir, card_id, tweet_url, _sha256_path(item["path"]))
                        if duplicate:
                            duplicate_rejections.append(duplicate)
                        else:
                            accepted.append(item)
                    if rejected:
                        _upsert_media_gap_block(
                            block_repo, card_id=card_id, source_url=tweet_url,
                            reason="x_media_unattributed",
                            details={"rejected_count": len(rejected), "rejected": rejected[:20]},
                        )
                    if duplicate_rejections:
                        _upsert_media_gap_block(
                            block_repo, card_id=card_id, source_url=tweet_url,
                            reason="x_media_cross_card_duplicate",
                            details={"rejected_count": len(duplicate_rejections), "matches": duplicate_rejections[:20]},
                        )
                    if accepted:
                        committed = _commit_staged_x_media(accepted, assets_dir, assets_root, tweet_id)
                        blocks = _create_media_blocks(
                            card_id,
                            tweet_url,
                            committed,
                            asset_repo,
                            block_repo,
                            sub_kind=sub_kind,
                            asset_provenance={"strategy": "opencli", "tweet_id": tweet_id},
                        )
                        _persist_x_opencli_remote_urls(asset_repo, card_id, committed)
                        return blocks
                    else:
                        opencli_reason = (
                            "x_media_cross_card_duplicate" if duplicate_rejections
                            else "x_media_unattributed" if rejected
                            else "x_media_download_no_media_files"
                        )
            else:
                opencli_reason = "x_media_download_no_json"
    finally:
        if attempt_dir is not None:
            shutil.rmtree(attempt_dir, ignore_errors=True)

    fallback = run_x_yt_dlp_fallback(tweet_url, tweet_id, assets_dir=assets_dir)
    if fallback.status == "downloaded":
        blocks = _create_media_blocks(
            card_id,
            tweet_url,
            fallback.files,
            asset_repo,
            block_repo,
            sub_kind=sub_kind,
            provenance_prefix="X yt-dlp",
            asset_provenance=fallback.provenance,
        )
        return blocks

    preserved_reasons = {
        "x_media_download_browser_session",
        "x_media_download_auth_required",
        "x_media_download_no_media_found",
        "x_media_unattributed",
        "x_media_cross_card_duplicate",
    }
    gap_reason = opencli_reason if opencli_reason in preserved_reasons else "x_media_download_fallback_failed"
    _upsert_media_gap_block(
        block_repo,
        card_id=card_id,
        source_url=tweet_url,
        reason=gap_reason,
        details={
            **lifecycle.details,
            "opencli_reason": opencli_reason,
            "fallback": fallback.evidence,
        },
    )
    return []


def _existing_verified_x_blocks(
    asset_repo: Any,
    block_repo: Any,
    assets_root: Path,
    card_id: str,
    tweet_id: str,
) -> list[dict[str, Any]]:
    valid_paths = {
        asset["local_path"] or ""
        for asset in asset_repo.list_for_card(card_id)
        if asset["status"] == "downloaded"
        and isinstance(asset.get("raw_payload"), dict)
        and asset["raw_payload"].get("tweet_id") == tweet_id
        and _persisted_asset_is_valid(assets_root, asset)
    }
    return [
        block
        for block in block_repo.list_for_card(card_id)
        if block["block_kind"] in {"image", "video"} and block["body"] in valid_paths
    ]


def _persisted_asset_is_valid(assets_root: Path, asset: dict[str, Any]) -> bool:
    path = assets_root / (asset["local_path"] or "")
    kind = asset["asset_kind"]
    expected_hash = (asset["content_hash"] or "").removeprefix("sha256:")
    return bool(
        path.is_file()
        and kind in {"image", "video"}
        and _looks_like_media_file(path, kind)
        and expected_hash
        and _sha256_path(path) == expected_hash
    )


def _commit_staged_x_media(
    staged: list[dict[str, Any]],
    assets_dir: Path,
    assets_root: Path,
    tweet_id: str,
) -> list[dict[str, Any]]:
    tweets_dir = assets_dir / "tweets"
    tweets_dir.mkdir(parents=True, exist_ok=True)
    tweets_dir.chmod(0o700)
    committed: list[dict[str, Any]] = []
    try:
        for item in staged:
            digest = _sha256_path(item["path"])
            target = tweets_dir / f"{tweet_id}_{digest[:16]}{item['path'].suffix.lower()}"
            fd, temporary_name = tempfile.mkstemp(prefix=".commit-", dir=tweets_dir)
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                shutil.copyfile(item["path"], temporary)
                temporary.chmod(0o600)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            committed.append({
                **item,
                "path": target,
                "relative_path": str(target.relative_to(assets_root)),
                "content_hash": digest,
            })
    except Exception:
        for item in committed:
            item["path"].unlink(missing_ok=True)
        raise
    return committed


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _list_asset_files(assets_dir: Path) -> list[dict[str, Any]]:
    """List downloaded media files in the assets directory."""
    tweets_dir = assets_dir / "tweets"
    if not tweets_dir.exists():
        return []

    files = []
    for path in sorted(tweets_dir.iterdir()):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            if not _looks_like_media_file(path, "image"):
                continue
            kind = "image"
            mime = f"image/{ext.lstrip('.')}"
        elif ext in {".mp4", ".mov", ".webm"}:
            if not _looks_like_media_file(path, "video"):
                continue
            kind = "video"
            mime = f"video/{ext.lstrip('.')}"
        else:
            continue

        size = path.stat().st_size
        files.append({
            "path": path,
            "relative_path": str(path.relative_to(assets_dir.parent)),
            "kind": kind,
            "mime": mime,
            "size": size,
        })

    return files
