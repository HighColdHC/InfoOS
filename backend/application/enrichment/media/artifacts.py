"""Media artifact persistence and shared file handling services."""
from __future__ import annotations

import hashlib
import re
import socket
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from urllib3.exceptions import HTTPError as Urllib3HTTPError

from backend.infrastructure.public_http import (
    open_public_url,
    open_pinned_url,
    read_public_url as read_bounded_public_url,
)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
_MEDIA_URL_KEYS = {"image", "image_url", "image_urls", "images", "thumbnail", "thumbnail_url", "media_url", "media_urls", "video", "video_url", "video_urls", "videos"}
_DIRECT_MEDIA_MAX_BYTES = 50 * 1024 * 1024
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5
PUBLIC_FETCH_ERRORS = (OSError, HTTPException, Urllib3HTTPError, ValueError)

class _PinnedTransport:
    """Compatibility seam for tests; production delegates to the pinned transport."""
    @staticmethod
    def get(url: str, **kwargs: Any):
        return open_pinned_url(url, **kwargs)


requests = _PinnedTransport()

def list_media_files(assets_dir: Path, *, relative_base: Path) -> list[dict[str, Any]]:
    if not assets_dir.exists():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(assets_dir.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in _IMAGE_EXTENSIONS:
            if not looks_like_media_file(path, "image"):
                continue
            kind = "image"
            mime = f"image/{ext.lstrip('.')}"
        elif ext in _VIDEO_EXTENSIONS:
            if not looks_like_media_file(path, "video"):
                continue
            kind = "video"
            mime = f"video/{ext.lstrip('.')}"
        else:
            continue
        files.append(
            {
                "path": path,
                "relative_path": str(path.relative_to(relative_base)),
                "kind": kind,
                "mime": mime,
                "size": path.stat().st_size,
            }
        )
    return files


def collect_generic_media_urls(card: dict[str, Any], base_dir: Path) -> list[str]:
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    from backend.application.enrichment.card_source import card_with_source_payload

    card = card_with_source_payload(card, base_dir)
    candidates: list[str] = []
    raw_payload = card.get("raw_payload") if isinstance(card.get("raw_payload"), dict) else {}
    candidates.extend(extract_media_urls(raw_payload))
    for block in CardContentBlockRepository(base_dir).list_for_card(card["card_id"]):
        candidates.extend(extract_media_urls(block["raw_payload"] if isinstance(block["raw_payload"], dict) else {}))
        if block["block_kind"] in {"image", "video"}:
            candidates.extend(extract_media_urls({"media_url": block["source_url"] or block["body"]}))
    seen: set[str] = set()
    urls: list[str] = []
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def extract_media_urls(value: Any, *, key_hint: str | None = None) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if is_direct_media_url(stripped, key_hint=key_hint):
            return [stripped]
        return []
    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(extract_media_urls(item, key_hint=key_hint))
        return urls
    if isinstance(value, dict):
        urls: list[str] = []
        for key, item in value.items():
            hint = str(key).lower()
            if hint in _MEDIA_URL_KEYS or any(part in hint for part in ("image", "video", "media", "thumbnail")):
                urls.extend(extract_media_urls(item, key_hint=hint))
            elif isinstance(item, (dict, list)):
                urls.extend(extract_media_urls(item, key_hint=hint))
        return urls
    return []


def is_direct_media_url(value: str, *, key_hint: str | None = None) -> bool:
    if not value.startswith(("http://", "https://")):
        return False
    path = urlparse(value).path.lower()
    ext = Path(path).suffix
    if ext in _IMAGE_EXTENSIONS or ext in _VIDEO_EXTENSIONS:
        return True
    return bool(key_hint and any(part in key_hint for part in ("image", "video", "media", "thumbnail")))


def download_direct_media_url(
    url: str,
    *,
    assets_dir: Path,
    index: int,
    allowed_hosts: frozenset[str] | None = None,
    max_bytes: int = _DIRECT_MEDIA_MAX_BYTES,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    return _download_public_media_url(
        url,
        assets_dir=assets_dir,
        index=index,
        allowed_hosts=allowed_hosts,
        max_bytes=max_bytes,
        headers=headers,
    )


def persist_media_content(
    content: bytes,
    *,
    source_url: str,
    mime_type: str,
    assets_dir: Path,
    index: int,
    max_bytes: int = _DIRECT_MEDIA_MAX_BYTES,
) -> dict[str, Any] | None:
    """Persist already-bounded media bytes using the shared artifact checks."""
    return _persist_media_chunks(
        (content,),
        current_url=source_url,
        mime_type=mime_type,
        assets_dir=assets_dir,
        index=index,
        max_bytes=max_bytes,
    )


def _download_public_media_url(
    url: str,
    *,
    assets_dir: Path,
    index: int,
    allowed_hosts: frozenset[str] | None,
    max_bytes: int,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    response: Any | None = None
    try:
        opened = _open_public_url(
            url,
            allowed_hosts=allowed_hosts,
            timeout=(10, 20),
            headers=headers or {"User-Agent": "InfoOS/0.1 (+https://local.infoos)"},
        )
        if opened is None:
            return None
        response, current_url = opened

        mime_type = str(response.headers.get("content-type") or "").split(";")[0].strip().lower()
        kind = media_kind_from_url_or_mime(current_url, mime_type)
        if kind is None:
            return None
        content_length = str(response.headers.get("content-length") or "")
        if content_length.isdigit() and int(content_length) > max_bytes:
            return None
        return _persist_media_chunks(
            response.iter_content(chunk_size=64 * 1024),
            current_url=current_url,
            mime_type=mime_type,
            assets_dir=assets_dir,
            index=index,
            max_bytes=max_bytes,
        )
    except PUBLIC_FETCH_ERRORS:
        (assets_dir / f".media_{index}.part").unlink(missing_ok=True)
        return None
    finally:
        if response is not None:
            response.close()


def _persist_media_chunks(
    chunks: Any,
    *,
    current_url: str,
    mime_type: str,
    assets_dir: Path,
    index: int,
    max_bytes: int,
) -> dict[str, Any] | None:
    kind = media_kind_from_url_or_mime(current_url, mime_type)
    if kind is None:
        return None
    assets_dir.mkdir(parents=True, exist_ok=True)
    temporary = assets_dir / f".media_{index}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("wb") as handle:
            temporary.chmod(0o600)
            for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("media byte limit exceeded")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            temporary.unlink(missing_ok=True)
            return None
        content_hash = digest.hexdigest()
        path = assets_dir / (
            f"media_{index}_{content_hash[:12]}"
            f"{media_suffix(current_url, mime_type, kind)}"
        )
        temporary.replace(path)
        path.chmod(0o600)
        if not looks_like_media_file(path, kind):
            path.unlink(missing_ok=True)
            return None
        return {
            "path": path,
            "kind": kind,
            "mime_type": mime_type
            or ("image/jpeg" if kind == "image" else "video/mp4"),
            "size_bytes": size,
            "content_hash": content_hash,
        }
    except (OSError, TypeError, ValueError):
        temporary.unlink(missing_ok=True)
        return None


def download_public_url_to_path(
    url: str,
    *,
    destination: Path,
    headers: dict[str, str] | None = None,
    timeout: int | tuple[float, float] = (10, 120),
    max_bytes: int = _DIRECT_MEDIA_MAX_BYTES,
) -> bool:
    """Stream one public HTTP(S) resource to disk after validating every hop."""
    response: Any | None = None
    temporary = destination.with_name(f".{destination.name}.part")
    try:
        opened = _open_public_url(url, allowed_hosts=None, timeout=timeout, headers=headers or {})
        if opened is None:
            return False
        response, _final_url = opened
        content_length = str(response.headers.get("content-length") or "")
        if content_length.isdigit() and int(content_length) > max_bytes:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with temporary.open("wb") as handle:
            temporary.chmod(0o600)
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("media byte limit exceeded")
                handle.write(chunk)
        if size == 0:
            return False
        temporary.replace(destination)
        destination.chmod(0o600)
        return True
    except PUBLIC_FETCH_ERRORS:
        return False
    finally:
        temporary.unlink(missing_ok=True)
        if response is not None:
            response.close()


def read_public_url(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int | tuple[float, float] = (3.05, 5),
    max_bytes: int = 5 * 1024 * 1024,
) -> tuple[bytes, str] | None:
    """Read one bounded public HTTP(S) response after validating every hop."""
    try:
        result = read_bounded_public_url(
            url,
            headers=headers or {},
            timeout=timeout,
            max_bytes=max_bytes,
            resolver=socket.getaddrinfo,
            opener=requests.get,
        )
    except PUBLIC_FETCH_ERRORS:
        return None
    if result is None:
        return None
    content, opened = result
    return content, opened.final_url


def _open_public_url(
    url: str,
    *,
    allowed_hosts: frozenset[str] | None,
    timeout: int | tuple[float, float],
    headers: dict[str, str],
) -> tuple[Any, str] | None:
    opened = open_public_url(
        url,
        allowed_hosts=allowed_hosts,
        timeout=timeout,
        headers=headers,
        max_redirects=_MAX_REDIRECTS,
        resolver=socket.getaddrinfo,
        opener=requests.get,
    )
    if opened is None:
        return None
    return opened.response, opened.final_url


def media_kind_from_url_or_mime(url: str, mime_type: str) -> str | None:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    ext = Path(urlparse(url).path.lower()).suffix
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    if ext in _VIDEO_EXTENSIONS:
        return "video"
    return None


def media_suffix(url: str, mime_type: str, kind: str) -> str:
    ext = Path(urlparse(url).path.lower()).suffix
    if re.fullmatch(r"\.[a-z0-9]{2,5}", ext):
        return ext
    if mime_type.startswith("image/png"):
        return ".png"
    if mime_type.startswith("image/gif"):
        return ".gif"
    if mime_type.startswith("image/webp"):
        return ".webp"
    if mime_type.startswith("video/webm"):
        return ".webm"
    if mime_type.startswith("video/quicktime"):
        return ".mov"
    return ".jpg" if kind == "image" else ".mp4"


def looks_like_media_file(path: Path, kind: str) -> bool:
    try:
        header = path.read_bytes()[:64]
    except OSError:
        return False
    stripped = header.lstrip().lower()
    if stripped.startswith((b"<!doctype html", b"<html", b"{", b"[")):
        return False
    if kind == "image":
        return header.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"RIFF"))
    if kind == "video":
        return b"ftyp" in header[:16] or header.startswith(b"\x1aE\xdf\xa3")
    return False


def create_media_blocks(
    card_id: str,
    source_url: str,
    files: list[dict[str, Any]],
    asset_repo: Any,
    block_repo: Any,
    *,
    sub_kind: str | None = None,
    provenance_prefix: str = "X",
    asset_provenance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Create card_assets rows and content blocks for downloaded media."""
    blocks = []
    sort_order = 1  # After original_tweet (0), before context blocks (2+)

    # Cache existing asset/block IDs to avoid per-file queries
    existing_asset_ids = get_existing_asset_ids(asset_repo, card_id)
    existing_block_ids = get_existing_block_ids(block_repo, card_id)

    for f in files:
        content_hash = str(f.get("content_hash") or _sha256_file(f["path"]))
        prefix = f"asset_{card_id}_{sub_kind}" if sub_kind else f"asset_{card_id}"
        asset_id = f"{prefix}_{f['kind']}_{f['path'].stem}" if sub_kind == "youtube" else f"{prefix}_{f['path'].stem}"
        block_id = f"block_{card_id}_{f['kind']}_{sub_kind or 'main'}_{f['path'].stem}"

        # Create asset record if not exists
        if asset_id not in existing_asset_ids:
            raw_payload = _whitelisted_x_provenance(asset_provenance)
            if sub_kind and not asset_provenance:
                raw_payload = {"sub_kind": sub_kind}
            asset_repo.upsert_asset({
                "asset_id": asset_id,
                "card_id": card_id,
                "asset_kind": f["kind"],
                "source_url": source_url,
                "local_path": f["relative_path"],
                "mime_type": f["mime"],
                "size_bytes": f["size"],
                "content_hash": f"sha256:{content_hash}",
                "preservation_class": "cached",
                "status": "downloaded",
                "cleanup_state": "present",
                "raw_payload": raw_payload,
            })

        # Create or refresh media content block with relative_path in body.
        # Refresh matters for historical rows created before quote/repost
        # media learned to mark themselves as embedded.
        raw_payload = {"asset_id": asset_id} if sub_kind == "douyin" else {}
        if sub_kind:
            parent_kind = "repost_original" if sub_kind == "repost" else sub_kind
            raw_payload = {
                "embedded_in_block_id": f"block_{card_id}_{parent_kind}",
                "embedded_in_block_kind": parent_kind,
                "sub_kind": sub_kind,
            }
            if sub_kind == "douyin":
                raw_payload["asset_id"] = asset_id
        block_repo.upsert_block({
            "block_id": block_id,
            "card_id": card_id,
            "position": sort_order,
            "layer": "media",
            "block_kind": f["kind"],
            "status": "downloaded",
            "body": f["relative_path"],
            "source_url": source_url,
            "provenance": f"{provenance_prefix} {f['kind']} — {f['path'].name}",
            "raw_payload": raw_payload,
        })

        blocks.append({
            "block_id": block_id,
            "card_id": card_id,
            "position": sort_order,
            "layer": "media",
            "block_kind": f["kind"],
            "status": "downloaded",
            "body": f["relative_path"],
            "source_url": source_url,
            "provenance": f"{provenance_prefix} {f['kind']} — {f['path'].name}",
            "raw_payload": raw_payload,
        })
        sort_order += 1

    return blocks


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _whitelisted_x_provenance(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    allowed = {
        "strategy",
        "aweme_id",
        "tweet_id",
        "video_id",
        "extractor",
        "version",
        "media_id",
        "uploader",
        "timestamp",
        "info_json_sha256",
    }
    return {key: value[key] for key in allowed if key in value and value[key] is not None}


def upsert_media_gap_block(
    block_repo: Any,
    *,
    card_id: str,
    source_url: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    block_repo.upsert_block(
        {
            "block_id": f"block_{card_id}_media_download_gap",
            "card_id": card_id,
            "position": next_media_block_position(block_repo, card_id),
            "layer": "media",
            "block_kind": "media_download_gap",
            "status": "blocked",
            "body": f"媒体下载未完成：{reason}",
            "segments": [],
            "source_url": source_url,
            "provenance": "media download gap",
            "raw_payload": {"reason": reason, "details": details or {}},
        }
    )


def get_existing_asset_ids(asset_repo: Any, card_id: str) -> set[str]:
    """Get existing asset IDs for a card to avoid duplicates."""
    with asset_repo.connect() as conn:
        rows = conn.execute(
            "SELECT asset_id FROM card_assets WHERE card_id = ?",
            (card_id,),
        ).fetchall()
        return {row[0] for row in rows}


def get_existing_block_ids(block_repo: Any, card_id: str) -> set[str]:
    """Get existing content block IDs for a card to avoid duplicates."""
    with block_repo.connect() as conn:
        rows = conn.execute(
            "SELECT block_id FROM card_content_blocks WHERE card_id = ?",
            (card_id,),
        ).fetchall()
        return {row[0] for row in rows}


def next_media_block_position(block_repo: Any, card_id: str) -> int:
    return max((block["position"] for block in block_repo.list_for_card(card_id)), default=0) + 1
