"""Server-side limits and URL safety rules for manual browser captures."""
from __future__ import annotations

from typing import Any

from backend.infrastructure.public_http import (
    select_supported_proxy,
    validate_and_pin_public_url,
)


CAPTURE_LIMITS = {
    "url_bytes": 4096,
    "title_bytes": 2048,
    "selected_text_bytes": 65536,
    "rendered_content_bytes": 2_097_152,
    "request_bytes": 2_228_224,
}


def capture_limit_error(payload: dict[str, Any]) -> str | None:
    """Return the error code of the first field over its declared limit."""
    for field, limit_key in (
        ("url", "url_bytes"),
        ("canonical_url", "url_bytes"),
        ("title", "title_bytes"),
        ("selected_text", "selected_text_bytes"),
    ):
        if len(str(payload.get(field) or "").encode("utf-8")) > CAPTURE_LIMITS[limit_key]:
            return f"{field}_too_large"
    rendered = payload.get("rendered_content")
    if isinstance(rendered, dict):
        rendered_size = sum(
            len(str(rendered.get(key) or "").encode("utf-8"))
            for key in ("html", "text", "byline", "excerpt", "site_name")
        )
        if rendered_size > CAPTURE_LIMITS["rendered_content_bytes"]:
            return "rendered_content_too_large"
    return None


def has_unsafe_capture_url(payload: dict[str, Any]) -> bool:
    return any(
        candidate and not is_public_capture_url(candidate)
        for candidate in (payload["url"], payload.get("canonical_url"))
    )


def is_public_capture_url(url: str) -> bool:
    return (
        validate_and_pin_public_url(
            url,
            allowed_hosts=None,
            proxy_url=select_supported_proxy(url),
        )
        is not None
    )
