from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
import tempfile
from typing import Any

from backend.application.enrichment.media.artifacts import create_media_blocks, upsert_media_gap_block
from backend.application.enrichment.media.tooling import run_yt_dlp_download
from backend.application.enrichment.transcripts.subtitles import parse_youtube_url, redact_youtube_diagnostic
from backend.infrastructure.runtime.environment import chrome_profile_dir
from backend.infrastructure.storage.paths import resolve_data_dir


def download_youtube_card_media(card_id: str, watch_url: str, base_dir: Path) -> list[dict[str, Any]]:
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    block_repo = CardContentBlockRepository(base_dir)
    parsed = parse_youtube_url(watch_url)
    if parsed is None:
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url="",
            reason="youtube_media_download_invalid_watch_url",
            details={"strategy": "yt_dlp"},
        )
        return []

    assets_root = resolve_data_dir(base_dir) / "assets"
    output_dir = assets_root / card_id / "youtube"
    output_dir.mkdir(parents=True, exist_ok=True)
    job_dir = Path(tempfile.mkdtemp(prefix=".video-", dir=output_dir))
    job_dir.chmod(0o700)
    result = run_yt_dlp_download(
        parsed.canonical_url,
        job_dir,
        cookies_from_browser=f"chrome:{chrome_profile_dir()}",
    )
    if result.status != "downloaded":
        shutil.rmtree(job_dir, ignore_errors=True)
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url=watch_url,
            reason=result.reason or "youtube_media_download_failed",
            details={"strategy": "yt_dlp", "status": result.status, "stderr": redact_youtube_diagnostic(result.stderr)},
        )
        return []
    info = result.metadata.get("info")
    if not _valid_youtube_download_info(info, parsed.video_id):
        shutil.rmtree(job_dir, ignore_errors=True)
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url=parsed.canonical_url,
            reason="youtube_media_download_info_mismatch",
            details={"strategy": "yt_dlp"},
        )
        return []
    video_files = [file for file in result.files if file.kind == "video"]
    if not video_files or any(file.size > 200 * 1024 * 1024 for file in video_files):
        shutil.rmtree(job_dir, ignore_errors=True)
        upsert_media_gap_block(
            block_repo,
            card_id=card_id,
            source_url=parsed.canonical_url,
            reason=(
                "youtube_media_download_video_rejected"
                if video_files
                else "youtube_media_download_no_video_file"
            ),
            details={"strategy": "yt_dlp", "thumbnail_only": not video_files},
        )
        return []

    files = [
        {
            "path": _persist_file(file.path, output_dir),
            "relative_path": "",
            "kind": file.kind,
            "mime": file.mime,
            "size": file.size,
        }
        for file in result.files
    ]
    for item in files:
        item["relative_path"] = item["path"].relative_to(assets_root).as_posix()
        item["content_hash"] = _sha256_file(item["path"])
    shutil.rmtree(job_dir, ignore_errors=True)
    asset_repo = CardAssetRepository(base_dir)
    blocks = create_media_blocks(
        card_id,
        parsed.canonical_url,
        files,
        asset_repo,
        block_repo,
        sub_kind="youtube",
        provenance_prefix="YouTube",
        asset_provenance={
            "strategy": "yt_dlp",
            "video_id": parsed.video_id,
            "extractor": str(info.get("extractor_key") or info.get("extractor") or ""),
            "version": result.metadata.get("version"),
            "media_id": str(info["id"]),
            "uploader": info.get("uploader"),
            "timestamp": info.get("timestamp"),
            "info_json_sha256": result.metadata.get("info_json_sha256"),
        },
    )
    _mark_youtube_assets(asset_repo, card_id)
    return blocks


def _mark_youtube_assets(asset_repo: Any, card_id: str) -> None:
    for asset in asset_repo.list_for_card(card_id):
        raw_payload = asset.get("raw_payload") if isinstance(asset.get("raw_payload"), dict) else {}
        if raw_payload.get("sub_kind") != "youtube":
            continue
        updated = dict(asset)
        updated["raw_payload"] = {**raw_payload, "strategy": "yt_dlp"}
        asset_repo.upsert_asset(updated)


def _valid_youtube_download_info(info: Any, video_id: str) -> bool:
    if not isinstance(info, dict) or str(info.get("id") or "") != video_id:
        return False
    extractors = [str(info.get(key) or "").lower() for key in ("extractor", "extractor_key")]
    if not any(value.startswith("youtube") for value in extractors):
        return False
    webpage = parse_youtube_url(str(info.get("webpage_url") or ""))
    return webpage is not None and webpage.video_id == video_id


def _persist_file(source: Path, output_dir: Path) -> Path:
    digest = _sha256_file(source)
    target = output_dir / f"{digest[:16]}{source.suffix.lower()}"
    shutil.move(str(source), target)
    target.chmod(0o600)
    return target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
