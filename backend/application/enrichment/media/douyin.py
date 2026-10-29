from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from backend.application.enrichment.media.candidates import collect_media_candidates
from backend.application.enrichment.media.douyin_ytdlp import (
    download_douyin_with_ytdlp,
    existing_douyin_video_blocks,
)
from backend.application.enrichment.media.artifacts import (
    download_direct_media_url,
    download_public_url_to_path,
    looks_like_media_file,
    next_media_block_position,
    upsert_media_gap_block,
)
from backend.domain.sources.douyin import parse_douyin_item_url
from backend.infrastructure.opencli.runtime import resolve_opencli_executable
from backend.infrastructure.opencli.tab_lease import opencli_tab_lease
from backend.infrastructure.security.redaction import redact_text
from backend.infrastructure.storage.paths import resolve_data_dir


_DOUYIN_RESOURCE_MAX_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class DouyinDownloadAttempt:
    downloaded: dict[str, Any] | None = None
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def download_douyin_card_media(card: dict[str, Any], base_dir: Path) -> list[dict[str, Any]]:
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    card_id = card["card_id"]
    source_url = card["source_url"]
    candidates = collect_media_candidates(card, base_dir)
    block_repo = CardContentBlockRepository(base_dir)
    asset_repo = CardAssetRepository(base_dir)
    assets_root = resolve_data_dir(base_dir) / "assets"
    assets_dir = assets_root / card_id / "douyin"
    assets_dir.mkdir(parents=True, exist_ok=True)

    existing = existing_douyin_video_blocks(card_id, base_dir)
    if existing:
        return existing

    for index, candidate in enumerate((item for item in candidates if item.kind == "video"), start=1):
        downloaded = download_direct_media_url(
            candidate.url,
            assets_dir=assets_dir,
            index=index,
            headers={"User-Agent": "Mozilla/5.0", "Referer": source_url},
        )
        if not downloaded or downloaded["kind"] != "video":
            continue
        block = _upsert_downloaded_douyin_asset(
            card=card,
            downloaded=downloaded,
            remote_url=candidate.url,
            assets_root=assets_root,
            asset_repo=asset_repo,
            block_repo=block_repo,
            strategy="direct_candidate",
            source=candidate.source,
            provenance=f"Douyin direct {downloaded['kind']} candidate",
        )
        return [block]

    ytdlp_blocks, ytdlp_reason = download_douyin_with_ytdlp(card, base_dir)
    if ytdlp_blocks:
        return ytdlp_blocks
    browser_attempt = _download_browser_resource_pair(card, assets_dir=assets_dir)
    if browser_attempt.downloaded and browser_attempt.downloaded["kind"] == "video":
        return [_upsert_downloaded_douyin_asset(
            card=card,
            downloaded=browser_attempt.downloaded,
            remote_url=browser_attempt.downloaded["remote_url"],
            assets_root=assets_root,
            asset_repo=asset_repo,
            block_repo=block_repo,
            strategy=browser_attempt.downloaded["strategy"],
            source="opencli_browser_resource",
            provenance="Douyin browser resource video candidate",
        )]
    upsert_media_gap_block(
        block_repo,
        card_id=card_id,
        source_url=source_url,
        reason=browser_attempt.reason or ytdlp_reason or "douyin_media_download_gap",
        details={
            "strategy": "trusted_video,direct_video,yt_dlp,browser_resource",
            "candidate_count": len(candidates),
            "yt_dlp_reason": ytdlp_reason,
            **browser_attempt.details,
        },
    )
    return []


def _upsert_downloaded_douyin_asset(
    *,
    card: dict[str, Any],
    downloaded: dict[str, Any],
    remote_url: str,
    assets_root: Path,
    asset_repo: Any,
    block_repo: Any,
    strategy: str,
    source: str,
    provenance: str,
) -> dict[str, Any]:
    card_id = card["card_id"]
    asset_id = f"asset_{card_id}_douyin_{downloaded['content_hash'][:16]}"
    local_path = str(downloaded["path"].relative_to(assets_root))
    safe_remote_url = _url_without_query(remote_url)
    asset_repo.upsert_asset(
        {
            "asset_id": asset_id,
            "card_id": card_id,
            "asset_kind": downloaded["kind"],
            "source_url": card["source_url"] or remote_url,
            "remote_url": safe_remote_url,
            "local_path": local_path,
            "mime_type": downloaded["mime_type"],
            "size_bytes": downloaded["size_bytes"],
            "content_hash": f"sha256:{downloaded['content_hash']}",
            "preservation_class": "cached",
            "status": "downloaded",
            "cleanup_state": "present",
            "raw_payload": {"strategy": strategy, "source": source},
        }
    )
    block = {
        "block_id": f"block_{card_id}_{downloaded['kind']}_douyin_{downloaded['content_hash'][:16]}",
        "card_id": card_id,
        "position": next_media_block_position(block_repo, card_id),
        "layer": "media",
        "block_kind": downloaded["kind"],
        "status": "downloaded",
        "body": local_path,
        "source_url": safe_remote_url,
        "provenance": provenance,
        "raw_payload": {"asset_id": asset_id, "strategy": strategy, "source": source},
    }
    block_repo.upsert_block(block)
    return block


def _download_browser_resource_pair(card: dict[str, Any], *, assets_dir: Path) -> DouyinDownloadAttempt:
    source_url = card["source_url"]
    if not source_url:
        return DouyinDownloadAttempt(reason="douyin_missing_source_url")
    pair, reason, details = _extract_douyin_resource_pair_from_browser(source_url)
    if not pair:
        return DouyinDownloadAttempt(reason=reason or "douyin_browser_no_media_resources", details=details)
    video_url = pair["video_url"]
    audio_url = pair.get("audio_url")
    if not audio_url:
        downloaded = download_direct_media_url(video_url, assets_dir=assets_dir, index=1)
        if downloaded:
            downloaded["strategy"] = "browser_video_resource"
            downloaded["remote_url"] = video_url
            return DouyinDownloadAttempt(downloaded=downloaded)
        return DouyinDownloadAttempt(
            reason="douyin_video_download_failed",
            details={"video_url": _url_without_query(video_url)},
        )
    return _download_and_merge_resource_pair(video_url, audio_url, assets_dir=assets_dir)


def _extract_douyin_resource_pair_from_browser(source_url: str) -> tuple[dict[str, str] | None, str | None, dict[str, Any]]:
    script = r"""(() => JSON.stringify({
      url: location.href,
      videos: [...document.querySelectorAll('video')].map(v => ({
        src: v.src,
        currentSrc: v.currentSrc,
        readyState: v.readyState,
        duration: Number.isFinite(v.duration) ? v.duration : null
      })),
      entries: performance.getEntriesByType('resource')
        .map(e => e.name)
        .filter(u => /douyinvod\.com|media-video|media-audio|mime_type=video_mp4/i.test(u))
        .slice(-100)
    }))()"""
    executable = resolve_opencli_executable("opencli")
    target_id: str | None = None
    try:
        with opencli_tab_lease("douyin"):
            tab_result = subprocess.run(
                [executable, "--profile", "infoos-fetch", "browser", "site:douyin", "tab", "new", source_url],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if tab_result.returncode != 0:
                return None, "douyin_browser_tab_open_failed", {"stderr": _safe_diagnostic(tab_result.stderr)}
            target_id = _parse_opencli_target_id(tab_result.stdout)
            if not target_id:
                return None, "douyin_browser_target_id_missing", {"stdout": _safe_diagnostic(tab_result.stdout)}
            tab_args = ["--tab", target_id]
            subprocess.run(
                [executable, "--profile", "infoos-fetch", "browser", "site:douyin", "wait", *tab_args, "time", "8"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            result = subprocess.run(
                [executable, "--profile", "infoos-fetch", "browser", "site:douyin", "eval", *tab_args, script],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, "douyin_browser_opencli_unavailable_or_timeout", {}
    finally:
        if target_id:
            try:
                subprocess.run(
                    [executable, "--profile", "infoos-fetch", "browser", "site:douyin", "tab", "close", target_id],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
    if result.returncode != 0:
        return None, "douyin_browser_eval_failed", {"stderr": _safe_diagnostic(result.stderr)}
    payload = _parse_opencli_json_output(result.stdout)
    if not isinstance(payload, dict):
        return None, "douyin_browser_invalid_eval_json", {"stdout": _safe_diagnostic(result.stdout)}
    expected_identity = parse_douyin_item_url(source_url)
    actual_identity = parse_douyin_item_url(str(payload.get("url") or ""))
    if (
        expected_identity is None
        or actual_identity is None
        or expected_identity.aweme_id != actual_identity.aweme_id
    ):
        return None, "douyin_browser_identity_mismatch", {
            "expected_url": _url_without_query(source_url),
            "actual_url": _url_without_query(str(payload.get("url") or "")),
        }
    pair = _select_douyin_resource_pair(payload.get("entries"))
    if not pair:
        return None, "douyin_browser_no_media_resources", {"entry_count": len(payload["entries"]) if isinstance(payload.get("entries"), list) else None}
    return pair, None, {"entry_count": len(payload["entries"]) if isinstance(payload.get("entries"), list) else None}


def _parse_opencli_target_id(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout.strip())
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("page", "targetId", "target_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in "{[":
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                for key in ("page", "targetId", "target_id", "id"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            continue
        parts = stripped.split()
        if len(parts) == 1 and all(ch.isalnum() or ch in {"-", "_"} for ch in parts[0]):
            return parts[0]
    return None


def _parse_opencli_json_output(stdout: str) -> Any:
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] not in "[{":
            continue
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            continue
    return None


def _select_douyin_resource_pair(entries: Any) -> dict[str, str] | None:
    if not isinstance(entries, list):
        return None
    video_urls: list[str] = []
    audio_urls: list[str] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.startswith(("http://", "https://")):
            continue
        lowered = entry.lower()
        if "douyinvod.com" not in lowered:
            continue
        if entry in video_urls or entry in audio_urls:
            continue
        if "media-audio" in lowered or "mp4a" in lowered:
            audio_urls.append(entry)
        elif "media-video" in lowered or "mime_type=video_mp4" in lowered:
            video_urls.append(entry)
    if not video_urls:
        return None
    pair = {"video_url": _prefer_highest_bitrate(video_urls)}
    if audio_urls:
        pair["audio_url"] = _prefer_highest_bitrate(audio_urls)
    return pair


def _prefer_highest_bitrate(urls: list[str]) -> str:
    def bitrate(url: str) -> int:
        marker = "&br="
        index = url.find(marker)
        if index < 0:
            index = url.find("?br=")
        if index < 0:
            return 0
        start = index + len(marker)
        end = start
        while end < len(url) and url[end].isdigit():
            end += 1
        try:
            return int(url[start:end])
        except ValueError:
            return 0

    return max(urls, key=bitrate)


def _download_and_merge_resource_pair(video_url: str, audio_url: str, *, assets_dir: Path) -> DouyinDownloadAttempt:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return DouyinDownloadAttempt(reason="douyin_ffmpeg_missing")
    assets_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = assets_dir / ".partial"
    temp_dir.mkdir(parents=True, exist_ok=True)
    video_path = temp_dir / "douyin_video.mp4"
    audio_path = temp_dir / "douyin_audio.m4a"
    merged_path = temp_dir / "douyin_merged.mp4"
    result: subprocess.CompletedProcess[str] | None = None
    try:
        if not _download_resource_to_path(video_url, video_path):
            return DouyinDownloadAttempt(
                reason="douyin_video_download_failed",
                details={"video_url": _url_without_query(video_url)},
            )
        if not _download_resource_to_path(audio_url, audio_path):
            return DouyinDownloadAttempt(
                reason="douyin_audio_download_failed",
                details={"audio_url": _url_without_query(audio_url)},
            )
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(merged_path),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return DouyinDownloadAttempt(reason="douyin_ffmpeg_timeout")
    finally:
        if result is None or result.returncode != 0 or not merged_path.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
    if result.returncode != 0:
        return DouyinDownloadAttempt(
            reason="douyin_ffmpeg_merge_failed",
            details={"stderr": _safe_diagnostic(result.stderr)},
        )
    if not merged_path.exists() or not looks_like_media_file(merged_path, "video"):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return DouyinDownloadAttempt(reason="douyin_ffmpeg_merge_invalid_output")
    try:
        content_hash = hashlib.sha256(merged_path.read_bytes()).hexdigest()
        final_path = assets_dir / f"media_1_{content_hash[:12]}.mp4"
        merged_path.replace(final_path)
        return DouyinDownloadAttempt(
            downloaded={
                "path": final_path,
                "kind": "video",
                "mime_type": "video/mp4",
                "size_bytes": final_path.stat().st_size,
                "content_hash": content_hash,
                "strategy": "browser_resource_pair",
                "remote_url": _url_without_query(video_url),
            }
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _safe_diagnostic(value: str) -> str:
    text = redact_text(value)
    return re.sub(
        r"https?://[^\s]+",
        lambda match: _url_without_query(match.group(0)),
        text,
        flags=re.IGNORECASE,
    )[:500]


def _url_without_query(value: str) -> str:
    trailing = value[-1] if value and value[-1] in ",.;)]" else ""
    raw = value[:-1] if trailing else value
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        if parsed.scheme.lower() not in {"http", "https"} or not hostname:
            return "[REDACTED_URL]"
        port = parsed.port
        netloc = f"{hostname}:{port}" if port is not None else hostname
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", "", "")) + trailing
    except ValueError:
        return "[REDACTED_URL]"


def _download_resource_to_path(url: str, path: Path) -> bool:
    temp_path = path.with_name(f"{path.name}.download")
    downloaded = download_public_url_to_path(
        url,
        destination=temp_path,
        headers={
            "User-Agent": "Mozilla/5.0 InfoOS/0.1",
            "Referer": "https://www.douyin.com/",
            "Accept": "*/*",
        },
        timeout=(10, 120),
        max_bytes=_DOUYIN_RESOURCE_MAX_BYTES,
    )
    if not downloaded:
        return False
    try:
        header = temp_path.read_bytes()[:64]
    except OSError:
        temp_path.unlink(missing_ok=True)
        return False
    if b"ftyp" not in header:
        temp_path.unlink(missing_ok=True)
        return False
    temp_path.replace(path)
    return True
