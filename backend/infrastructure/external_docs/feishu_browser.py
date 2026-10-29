from __future__ import annotations

import base64
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
import ipaddress
import json
from typing import Any, Literal
from urllib.parse import urlparse

from backend.infrastructure.opencli.commands import run_opencli
from backend.domain.failures import OpenCLISiteLeaseTimeout
from backend.infrastructure.opencli.tab_lease import current_opencli_context_id, opencli_tab_lease


FetchStatus = Literal["fetched", "pending", "blocked"]
_ALLOWED_DOMAINS = ("feishu.cn", "larksuite.com")
_MAX_BODY_CHARS = 120_000
_MAX_MEDIA_URLS = 200
_MAX_AUTHENTICATED_MEDIA_ITEMS = 20
_MAX_AUTHENTICATED_MEDIA_BYTES = 32 * 1024 * 1024
_MAX_AUTHENTICATED_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_AUTHENTICATED_VIDEO_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class FeishuMediaBlob:
    url: str
    kind: Literal["image", "video"]
    mime_type: str
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class FeishuDocumentResult:
    status: FetchStatus
    source_url: str
    body: str = ""
    complete: bool = False
    blocks: int = 0
    reached_end: bool = False
    images: list[str] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)
    media_blobs: list[FeishuMediaBlob] = field(default_factory=list)
    reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


def fetch_feishu_document(url: str, *, timeout: int = 230) -> FeishuDocumentResult:
    """Read bounded full Feishu/Lark document content through InfoOS Chrome.

    This adapter never reads cookies, localStorage, or tokens. The dedicated
    browser profile supplies its own session, while the returned payload is
    limited to visible text and public media URL facts.
    """
    if not is_allowed_feishu_url(url):
        return _result("blocked", url, "external_doc_url_not_allowed")

    try:
        with ExitStack() as stack:
            try:
                stack.enter_context(opencli_tab_lease(
                    "feishu", timeout_seconds=min(timeout, 30), close_tabs=False,
                ))
            except TimeoutError as exc:
                # Profile-read and site acquisition share the same contention path.
                raise OpenCLISiteLeaseTimeout("feishu") from exc
            profile_context = current_opencli_context_id()
            fetched = run_opencli(
                [
                    "--profile",
                    profile_context,
                    "feishu",
                    "document",
                    url,
                    "--budget-seconds",
                    "170",
                    "--timeout",
                    "180",
                    "--format",
                    "json",
                    "--window",
                    "background",
                    "--site-session",
                    "ephemeral",
                    "--keep-tab",
                    "false",
                ],
                timeout=timeout,
            )
    except OpenCLISiteLeaseTimeout:
        raise
    except Exception as exc:  # noqa: BLE001 - Browser subprocess failures must become structured external-document results
        return _result(
            "pending",
            url,
            "external_doc_browser_exception",
            diagnostics={"error_class": type(exc).__name__},
        )

    if fetched.returncode != 0:
        return _command_failure(url, "external_doc_adapter_failed", fetched.returncode)
    payload = _adapter_record(_first_json_value(fetched.stdout))
    if not isinstance(payload, dict):
        return _result("pending", url, "external_doc_adapter_invalid_output")

    final_url = str(payload.get("url") or "").strip()
    if not is_allowed_feishu_url(final_url):
        return _result("blocked", url, "external_doc_redirect_not_allowed")
    body = str(payload.get("body") or "").strip()[:_MAX_BODY_CHARS]
    if bool(payload.get("login_required")):
        return _result("blocked", url, "external_doc_login_required")
    if not body:
        return _result("blocked", url, "external_doc_empty")
    complete = (payload.get("complete") is True and payload.get("completeness") == "complete"
                and payload.get("reached_end") is True and len(body) < _MAX_BODY_CHARS)
    media_blobs = _bounded_media_blobs(payload.get("media_blobs"))
    return FeishuDocumentResult(
        status="fetched",
        source_url=final_url,
        body=body,
        complete=complete,
        blocks=max(0, int(payload.get("blocks") or 0)),
        reached_end=payload.get("reached_end") is True,
        reason=None if complete else "external_doc_partial",
        images=_bounded_media_urls(payload.get("images")),
        videos=_bounded_media_urls(payload.get("videos")),
        media_blobs=media_blobs,
        diagnostics={
            "body_chars": len(body),
            "authenticated_media_count": len(media_blobs),
            "authenticated_media_bytes": sum(len(blob.content) for blob in media_blobs),
        },
    )


def is_allowed_feishu_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return False
        return any(hostname == domain or hostname.endswith(f".{domain}") for domain in _ALLOWED_DOMAINS)
    except ValueError:
        return False


def _adapter_record(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict) and "session" in payload and "data" in payload:
        payload = payload.get("data")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict)), None)
    return payload if isinstance(payload, dict) else None


def _first_json_value(stdout: str) -> Any:
    stripped = stdout.strip()
    if stripped:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    for line in stdout.splitlines():
        candidate = line.strip()
        if not candidate or candidate[0] not in "[{\"":
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _bounded_media_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        url = str(raw or "").strip()
        if not _is_public_http_url(url) or url in seen:
            continue
        result.append(url)
        seen.add(url)
        if len(result) >= _MAX_MEDIA_URLS:
            break
    return result


def _bounded_media_blobs(
    value: Any, *, read_reference: Callable[[dict], bytes] | None = None,
    rejected: list[dict[str, Any]] | None = None,
) -> list[FeishuMediaBlob]:
    if not isinstance(value, list):
        return []
    result: list[FeishuMediaBlob] = []
    seen: set[str] = set()
    total_bytes = 0
    for raw in value:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        mime_type = str(raw.get("mime_type") or "").split(";", 1)[0].strip().lower()
        def reject(reason: str) -> None:
            if rejected is not None:
                rejected.append({"url": url, "mime_type": mime_type,
                                 "kind": kind, "reason": reason})
        kind: Literal["image", "video"] | None = None
        if mime_type.startswith("image/"):
            kind = "image"
            max_bytes = _MAX_AUTHENTICATED_IMAGE_BYTES
        elif mime_type.startswith("video/"):
            kind = "video"
            max_bytes = _MAX_AUTHENTICATED_VIDEO_BYTES
        else:
            reject("unsupported_mime_type")
            continue
        if not is_allowed_feishu_url(url):
            reject("media_url_not_allowed")
            continue
        if url in seen:
            reject("duplicate_media_url")
            continue
        if len(result) >= _MAX_AUTHENTICATED_MEDIA_ITEMS:
            reject("media_item_limit")
            continue
        if raw.get("reason") and "base64" not in raw and "sha256" not in raw:
            reject(str(raw["reason"]))
            continue
        try:
            if "base64" in raw:
                encoded = str(raw.get("base64") or "")
                if len(encoded) > 4 * ((max_bytes + 2) // 3):
                    reject("media_size_limit")
                    continue
                content = base64.b64decode(encoded, validate=True)
            elif read_reference is not None:
                content = read_reference(raw)
            else:
                reject("media_reference_unavailable")
                continue
        except (ValueError, OSError):
            reject("invalid_media_content_or_reference")
            continue
        if not content:
            reject("empty_media_content")
            continue
        if len(content) > max_bytes or total_bytes + len(content) > _MAX_AUTHENTICATED_MEDIA_BYTES:
            reject("media_size_limit")
            continue
        result.append(FeishuMediaBlob(url=url, kind=kind, mime_type=mime_type, content=content))
        seen.add(url)
        total_bytes += len(content)
    return result


def _is_public_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return True
        return address.is_global
    except ValueError:
        return False


def _command_failure(url: str, reason: str, returncode: int) -> FeishuDocumentResult:
    return _result("pending", url, reason, diagnostics={"returncode": int(returncode)})


def _result(
    status: FetchStatus,
    url: str,
    reason: str,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> FeishuDocumentResult:
    return FeishuDocumentResult(
        status=status,
        source_url=url,
        reason=reason,
        diagnostics=diagnostics or {},
    )
