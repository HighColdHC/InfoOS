"""Cookie-free short-link resolution over the existing pinned public transport."""
import http.client

from urllib3.exceptions import HTTPError

from backend.domain.capture_links import SHORT_LINK_PLATFORM_HOSTS, short_link_platform
from backend.domain.captures import platform_item_identity
from backend.infrastructure.public_http import resolve_public_url


def resolve_capture_link(url: str) -> dict[str, str]:
    platform = short_link_platform(url)
    if platform is None:
        return {"resolved_url": url, "resolution": "not_needed"}
    try:
        result = resolve_public_url(
            url, allowed_hosts=SHORT_LINK_PLATFORM_HOSTS[platform],
            terminal_url=lambda target: bool(platform_item_identity(platform, target)),
            headers={}, timeout=5, max_redirects=5,
        )
        if result and platform_item_identity(platform, result.final_url):
            return {"resolved_url": result.final_url, "resolution": "resolved"}
        reason = "短链未解析到安全的同平台条目，或重定向超过5跳"
    except (HTTPError, OSError, ValueError, http.client.HTTPException):
        # Do not expose transport exceptions: they may contain proxy credentials.
        reason = "短链解析请求失败或超时"
    return {"resolved_url": url, "resolution": "failed", "resolution_reason": reason}
