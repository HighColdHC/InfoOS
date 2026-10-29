from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.types import Task, TaskStatus
from backend.domain.media_outcomes import is_resolved_x_no_media_outcome
from backend.infrastructure.security.redaction import summarize_subprocess_output


def handle_media_download(task: Task, base_dir: Path) -> dict[str, Any]:
    from backend.application.enrichment.media.candidates import card_expects_downloadable_media, collect_media_candidates
    from backend.application.enrichment.media.download import download_card_media
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository

    card = CardRepository(base_dir).get_card(task.target_id) or {}
    initial_blocks = CardContentBlockRepository(base_dir).list_for_card(task.target_id)
    if card.get("source_platform") == "youtube":
        from backend.application.enrichment.youtube_blocks import has_usable_youtube_transcript

        if has_usable_youtube_transcript(initial_blocks):
            resolve_media_gap_blocks(CardContentBlockRepository(base_dir), task.target_id, initial_blocks)
            return {
                "status": TaskStatus.SUCCESS.value,
                "artifacts": {
                    "card_id": task.target_id,
                    "asset_id": None,
                    "asset_kind": "mixed",
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "failure_class": None,
                    "assets_downloaded": 0,
                    "assets": [],
                    "media_available": False,
                    "download_status": "skipped_transcript_available",
                    "strategy": "youtube_platform_subtitle",
                    "candidate_count": 0,
                    "gap_reasons": [],
                },
            }
    candidate_count = len(collect_media_candidates(card, base_dir)) if card else 0
    from backend.application.enrichment.card_source import card_with_source_payload

    source_card = card_with_source_payload(card, base_dir) if card else {}
    expects_media = bool(source_card and card_expects_downloadable_media(source_card))
    asset_repo = CardAssetRepository(base_dir)
    block_repo = CardContentBlockRepository(base_dir)
    existing_assets = asset_repo.list_for_card(task.target_id)
    if card and not expects_media and candidate_count == 0 and not existing_assets:
        resolve_media_gap_blocks(block_repo, task.target_id, initial_blocks)
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {
                "card_id": task.target_id,
                "asset_id": None,
                "asset_kind": "mixed",
                "total": 0,
                "success": 0,
                "failed": 0,
                "failure_class": None,
                "assets_downloaded": 0,
                "assets": [],
                "media_available": False,
                "download_status": "skipped_no_media_expected",
                "strategy": None,
                "candidate_count": 0,
                "gap_reasons": [],
            },
        }
    before = {asset["asset_id"] for asset in asset_repo.list_for_card(task.target_id)}
    before_block_ids = {block["block_id"] for block in initial_blocks}
    download_card_media(task.target_id, base_dir)
    assets = asset_repo.list_for_card(task.target_id)
    blocks = block_repo.list_for_card(task.target_id)
    blocks = _normalize_media_gap_diagnostics(block_repo, blocks)
    usable_assets = assets
    if card and card.get("source_platform") == "douyin":
        from backend.application.enrichment.media.artifacts import looks_like_media_file
        from backend.infrastructure.storage.paths import resolve_data_dir

        assets_root = resolve_data_dir(base_dir) / "assets"
        usable_assets = [
            asset
            for asset in assets
            if _usable_douyin_video_asset(
                asset,
                assets_root=assets_root,
                looks_like_media_file=looks_like_media_file,
            )
        ]
    if usable_assets:
        resolve_media_gap_blocks(block_repo, task.target_id, blocks)
        blocks = block_repo.list_for_card(task.target_id)
    media_gap_blocks = [
        block
        for block in blocks
        if block["block_kind"] == "media_download_gap"
        and (
            block["block_id"] not in before_block_ids
            or block["status"] == "blocked"
        )
    ]
    gap_reasons = []
    for block in media_gap_blocks:
        raw_payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
        reason = raw_payload.get("reason") or raw_payload.get("block_reason")
        if reason:
            gap_reasons.append(str(reason))
    new_or_existing = [
        {
            "asset_id": asset["asset_id"],
            "asset_kind": asset["asset_kind"],
            "local_path": asset.get("local_path"),
            "mime_type": asset.get("mime_type"),
            "status": asset["status"],
        }
        for asset in usable_assets
        if asset["asset_id"] not in before or asset["status"] in {"downloaded", "understood"}
    ]
    artifact_gap_reasons = [] if new_or_existing else sorted(set(gap_reasons))
    stderr_summary = _media_stderr_summary(media_gap_blocks)
    browser_session_gap = "x_media_download_browser_session" in artifact_gap_reasons
    strategy = strategy_from_media_gap_blocks(media_gap_blocks) or strategy_from_assets(usable_assets)
    download_status = media_download_status(
        media_available=bool(new_or_existing),
        gap_reasons=gap_reasons,
        expects_media=expects_media,
        candidate_count=candidate_count,
    )
    if download_status == "failed_download_error" and card["source_platform"] == "github":
        from backend.application.enrichment.card_source import matching_raw_item
        from backend.infrastructure.storage.repositories import RawItemRepository

        raw = matching_raw_item(RawItemRepository(base_dir), source_card)
        if raw is not None and raw.raw_metadata.get("capture_adapter") == "github:item":
            # README/body media is optional. Keep the gap and finalize as partial.
            download_status = "skipped_optional_media_unavailable"
    if is_resolved_x_no_media_outcome(
        source_platform=card.get("source_platform") if card else None,
        download_status=download_status,
        candidate_count=candidate_count,
        reasons=gap_reasons,
    ):
        resolve_media_gap_blocks(
            block_repo,
            task.target_id,
            media_gap_blocks,
            reasons={"x_media_download_no_media_found"},
        )
    task_status = TaskStatus.SUCCESS.value
    if download_status == "failed_download_error":
        task_status = TaskStatus.FAILED.value
    elif download_status.startswith("blocked_") or download_status.startswith("skipped_"):
        task_status = TaskStatus.SKIPPED.value
    return {
        "status": task_status,
        "artifacts": {
            "card_id": task.target_id,
            "asset_id": new_or_existing[0]["asset_id"] if len(new_or_existing) == 1 else None,
            "asset_kind": new_or_existing[0]["asset_kind"] if len(new_or_existing) == 1 else "mixed",
            "total": len(usable_assets),
            "success": sum(asset["status"] in {"downloaded", "understood"} for asset in usable_assets),
            "failed": sum(asset["status"] in {"failed", "blocked", "skipped"} for asset in assets),
            "failure_class": download_status if task_status != TaskStatus.SUCCESS.value else None,
            "assets_downloaded": len([asset for asset in usable_assets if asset["asset_id"] not in before]),
            "assets": new_or_existing,
            "media_available": bool(new_or_existing),
            "download_status": download_status,
            "strategy": strategy,
            "candidate_count": candidate_count,
            "gap_reasons": artifact_gap_reasons,
            **({"stderr_summary": stderr_summary} if stderr_summary else {}),
            **({
                "failure_reason": "x_media_download_browser_session",
                "user_action": "Reconnect the OpenCLI profile, then retry media/card.",
                "diagnostic_reference": _gap_diagnostic_reference(media_gap_blocks),
            } if browser_session_gap else {}),
        },
    }


def _normalize_media_gap_diagnostics(block_repo: Any, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove legacy raw stream fields before a media task records its result."""
    normalized: list[dict[str, Any]] = []
    for block in blocks:
        payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        if block["block_kind"] != "media_download_gap" or not ({"stderr", "stdout"} & details.keys()):
            normalized.append(block)
            continue
        summary = summarize_subprocess_output(details.get("stdout"), details.get("stderr"))
        safe_details = {key: value for key, value in details.items() if key not in {"stderr", "stdout"}}
        if summary:
            safe_details["stderr_summary"] = summary
        updated = {**block, "raw_payload": {**payload, "details": safe_details}}
        block_repo.upsert_block(updated)
        normalized.append(updated)
    return normalized


def _media_stderr_summary(blocks: list[dict[str, Any]]) -> str | None:
    for block in blocks:
        payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        summary = details.get("stderr_summary")
        if isinstance(summary, str) and summary:
            return summary
    return None


def _usable_douyin_video_asset(
    asset: dict[str, Any],
    *,
    assets_root: Path,
    looks_like_media_file: Any,
) -> bool:
    if (
        asset["asset_kind"] != "video"
        or asset["status"] not in {"downloaded", "understood"}
        or not asset.get("local_path")
    ):
        return False
    try:
        path = (assets_root / str(asset["local_path"])).resolve()
        path.relative_to(assets_root.resolve())
    except (OSError, ValueError):
        return False
    return path.is_file() and path.stat().st_size > 0 and bool(looks_like_media_file(path, "video"))


def resolve_media_gap_blocks(
    block_repo: Any,
    card_id: str,
    blocks: list[dict[str, Any]],
    *,
    reasons: set[str] | None = None,
) -> None:
    for block in blocks:
        if block["block_kind"] != "media_download_gap" or block["status"] != "blocked":
            continue
        raw_payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
        reason = str(raw_payload.get("reason") or raw_payload.get("block_reason") or "")
        if reasons is not None and reason not in reasons:
            continue
        block_repo.upsert_block(
            {
                **block,
                "status": "resolved",
                "body": "媒体下载已完成",
                "raw_payload": raw_payload | {"resolved": True},
            }
        )


def media_download_status(
    *,
    media_available: bool,
    gap_reasons: list[str],
    expects_media: bool,
    candidate_count: int,
) -> str:
    if media_available:
        return "downloaded"
    reason_set = set(gap_reasons)
    if reason_set & {"yt_dlp_missing", "gallery_dl_missing", "tool_missing", "xiaohongshu_media_download_opencli_missing"}:
        return "blocked_missing_tool"
    if reason_set & {"auth_required", "x_media_download_auth_required"}:
        return "blocked_auth_required"
    if reason_set & {
        "x_media_download_no_media_found",
        "x_media_download_no_media_files",
        "x_media_download_fallback_failed",
        "x_media_download_invalid_status_url",
        "youtube_media_download_no_video_file",
        "youtube_media_download_video_rejected",
        "douyin_ytdlp_no_video_file",
    }:
        return "skipped_no_media_found"
    if "x_media_download_browser_session" in reason_set:
        return "skipped_browser_session"
    if gap_reasons:
        return "failed_download_error"
    if expects_media and candidate_count == 0:
        return "blocked_no_candidates"
    return "skipped_no_media_expected"


def strategy_from_media_gap_blocks(blocks: list[dict[str, Any]]) -> str | None:
    for block in blocks:
        payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        strategy = details.get("strategy")
        if strategy:
            return str(strategy)
    return None


def strategy_from_assets(assets: list[dict[str, Any]]) -> str | None:
    for asset in assets:
        payload = asset.get("raw_payload") if isinstance(asset.get("raw_payload"), dict) else {}
        strategy = payload.get("strategy")
        if strategy:
            return str(strategy)
    return None


def _gap_diagnostic_reference(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    for block in blocks:
        payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
        if payload.get("reason") == "x_media_download_browser_session":
            details = payload.get("details")
            return dict(details) if isinstance(details, dict) else {}
    return {}
