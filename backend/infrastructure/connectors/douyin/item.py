"""Single-item Douyin provider backed by yt-dlp metadata."""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

import json
from pathlib import Path
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps
from backend.domain.sources.douyin import (
    DOUYIN_INPUT_HOSTS,
    DouyinItemIdentity,
    extract_single_douyin_url,
    parse_douyin_item_url,
    sanitized_douyin_url_evidence,
)
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.public_http import resolve_public_url
from backend.infrastructure.runtime.environment import chrome_profile_dir


@dataclass
class DouyinItemConnector:
    connector_id: str
    identifier: str
    timeout_seconds: int = 120
    discovery_evidence: dict[str, Any] = field(
        init=False, default_factory=lambda: {"availability": "unavailable"}
    )

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        identity = resolve_douyin_item_identity(self.identifier)
        metadata = load_douyin_item_metadata(identity, timeout_seconds=self.timeout_seconds)
        item = raw_item_from_douyin_metadata(self.connector_id, identity, metadata)
        self.discovery_evidence = {
            "executor": "yt_dlp",
            "sanitized_arguments": [identity.canonical_url],
            "links": [identity.canonical_url],
            "discovered_link_count": 1,
            "availability": "available",
        }
        items = [] if since_cursor == identity.canonical_url else [item]
        return FetchResult(
            items=items,
            next_cursor=identity.canonical_url,
            visible_count=1,
            new_count=len(items),
            dedup_skipped_count=1 - len(items),
        )


def resolve_douyin_item_identity(value: str) -> DouyinItemIdentity:
    direct = parse_douyin_item_url(value)
    if direct is not None:
        return direct
    candidate = extract_single_douyin_url(value)
    resolved = resolve_public_url(
        candidate,
        allowed_hosts=DOUYIN_INPUT_HOSTS,
        terminal_url=lambda url: parse_douyin_item_url(url) is not None,
        headers={"User-Agent": "Mozilla/5.0 InfoOS/0.1"},
        timeout=(5, 15),
        max_redirects=5,
    )
    if resolved is None:
        raise RuntimeError(
            f"Douyin short URL could not be resolved: {sanitized_douyin_url_evidence(candidate)}"
        )
    identity = parse_douyin_item_url(resolved.final_url)
    if identity is None:
        raise RuntimeError("Douyin short URL did not resolve to a supported canonical video URL")
    return identity


def load_douyin_item_metadata(
    identity: DouyinItemIdentity,
    *,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    executable = shutil.which("yt-dlp")
    if executable is None:
        venv_candidate = Path(sys.executable).with_name("yt-dlp")
        executable = str(venv_candidate) if venv_candidate.is_file() else None
    if executable is None:
        raise RuntimeError("yt-dlp executable not found")
    command = [
        executable,
        "--ignore-config",
        "--no-playlist",
        "--skip-download",
        "--dump-single-json",
        "--cookies-from-browser",
        f"chrome:{chrome_profile_dir()}",
        identity.canonical_url,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("yt-dlp could not read Douyin item metadata") from exc
    if result.returncode != 0:
        raise RuntimeError("yt-dlp could not read Douyin item metadata")
    try:
        metadata = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("yt-dlp returned invalid Douyin item metadata") from exc
    if not isinstance(metadata, dict) or not valid_douyin_metadata(metadata, identity):
        raise RuntimeError("yt-dlp returned mismatched Douyin item metadata")
    return metadata


def valid_douyin_metadata(metadata: dict[str, Any], identity: DouyinItemIdentity) -> bool:
    extractors = [str(metadata.get(key) or "").lower() for key in ("extractor", "extractor_key")]
    webpage = parse_douyin_item_url(str(metadata.get("webpage_url") or ""))
    return (
        str(metadata.get("id") or "") == identity.aweme_id
        and any("douyin" in extractor for extractor in extractors)
        and webpage is not None
        and webpage.aweme_id == identity.aweme_id
    )


def raw_item_from_douyin_metadata(
    connector_id: str,
    identity: DouyinItemIdentity,
    metadata: dict[str, Any],
) -> RawItem:
    timestamp = _timestamp(metadata.get("timestamp"))
    title = _text(metadata.get("title"))
    description = _text(metadata.get("description"))
    uploader = _text(metadata.get("uploader"))
    detail_record = {
        "aweme_id": identity.aweme_id,
        "title": title,
        "description": description,
        "uploader": uploader,
        "uploader_id": _text(metadata.get("uploader_id")),
        "timestamp": metadata.get("timestamp"),
        "duration": metadata.get("duration"),
        "formats": _safe_formats(metadata.get("formats")),
        "thumbnails": _safe_thumbnails(metadata.get("thumbnails")),
        "extractor": _text(metadata.get("extractor_key") or metadata.get("extractor")),
        "webpage_url": identity.canonical_url,
    }
    return RawItem(
        raw_item_id=identity.canonical_url,
        connector_id=connector_id,
        source_platform="douyin",
        source_type="item",
        source_url=identity.canonical_url,
        captured_at=utc_now_micro_z(),
        raw_text=description or title or "",
        raw_author=RawAuthor(name=uploader, handle=_text(metadata.get("uploader_id"))),
        raw_timestamps=RawTimestamps(published_at=timestamp),
        raw_metadata={
            "title": title,
            "description": description,
            "duration": metadata.get("duration"),
            "formats": detail_record["formats"],
            "thumbnails": detail_record["thumbnails"],
            "douyin_detail_record": detail_record,
        },
    )


def _safe_formats(value: Any) -> list[dict[str, Any]]:
    keys = ("format_id", "ext", "width", "height", "fps", "vcodec", "acodec", "filesize", "url")
    return _safe_media_entries(value, keys)


def _safe_thumbnails(value: Any) -> list[dict[str, Any]]:
    keys = ("id", "width", "height", "preference", "url")
    return _safe_media_entries(value, keys)


def _safe_media_entries(value: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            continue
        projected = {key: entry.get(key) for key in keys if entry.get(key) not in (None, "")}
        if "url" in projected:
            projected["url"] = _strip_url_evidence(str(projected["url"]))
            if not projected["url"] or projected["url"] in seen_urls:
                continue
            seen_urls.add(str(projected["url"]))
        if projected:
            output.append(projected)
    return output


def _strip_url_evidence(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", "", ""))


def _timestamp(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
