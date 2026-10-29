"""Read-only Wigolo shadow adapter for public-document comparison."""
from __future__ import annotations

from hashlib import sha256
import http.client
import json
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlsplit

from backend.domain.public_documents import (
    PublicDocument,
    PublicDocumentFailure,
    PublicDocumentOutcome,
    PublicDocumentRequest,
)
from backend.infrastructure.public_http import (
    select_supported_proxy,
    validate_and_pin_public_url,
)


class WigoloPublicDocumentProvider:
    name = "wigolo"

    def __init__(
        self,
        *,
        base_url: str,
        token_file: Path,
        timeout_seconds: float = 45,
    ):
        self._base_url = _validated_base_url(base_url)
        self._token_file = token_file
        self._timeout_seconds = min(120.0, max(1.0, float(timeout_seconds)))

    def fetch(self, request: PublicDocumentRequest) -> PublicDocumentOutcome:
        started = time.monotonic()
        if validate_and_pin_public_url(
            request.url,
            allowed_hosts=None,
            proxy_url=select_supported_proxy(request.url),
        ) is None:
            return self._failure("unsafe_url", started)
        try:
            token = _read_token(self._token_file)
            status, payload = self._post_fetch(
                token=token,
                url=request.url,
                timeout=self._timeout_seconds,
                max_bytes=request.max_bytes,
            )
        except TimeoutError:
            return self._failure("timeout", started)
        except (OSError, ValueError, json.JSONDecodeError):
            return self._failure("provider_error", started)
        if status != 200:
            return self._failure("http_error", started)
        markdown = str(payload.get("markdown") or "").strip()
        if not markdown:
            return self._failure("empty_content", started)
        final_url = str(payload.get("url") or request.url).strip()
        if validate_and_pin_public_url(
            final_url,
            allowed_hosts=None,
            proxy_url=select_supported_proxy(final_url),
        ) is None:
            return self._failure("unsafe_final_url", started)
        return PublicDocumentOutcome(
            document=PublicDocument(
                provider=self.name,
                final_url=final_url,
                title=_optional_text(payload.get("title")),
                content=markdown,
                readable_html=None,
                links=_public_urls(payload.get("links")),
                images=_public_urls(payload.get("images")),
                videos=(),
                fetch_tier=str(payload.get("fetch_method") or "http"),
                cache_hit=bool(payload.get("cached")),
                status_code=_optional_int(payload.get("http_status")),
                elapsed_ms=_elapsed_ms(started),
                response_bytes=None,
                content_hash=str(payload.get("content_hash") or "").strip()
                or sha256(markdown.encode("utf-8")).hexdigest(),
            )
        )

    def _post_fetch(
        self,
        *,
        token: str,
        url: str,
        timeout: float,
        max_bytes: int,
    ) -> tuple[int, dict[str, Any]]:
        parsed = urlsplit(self._base_url)
        connection_class = (
            http.client.HTTPSConnection
            if parsed.scheme.lower() == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(
            parsed.hostname or "",
            parsed.port,
            timeout=timeout,
        )
        body = json.dumps(
            {"url": url, "render_js": "never"},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            connection.request(
                "POST",
                "/v1/fetch",
                body=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("wigolo_response_too_large")
            decoded = json.loads(raw.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("wigolo_response_not_object")
            return int(response.status), decoded
        finally:
            connection.close()

    def _failure(self, failure_class: str, started: float) -> PublicDocumentOutcome:
        return PublicDocumentOutcome(
            failure=PublicDocumentFailure(
                provider=self.name,
                failure_class=failure_class,
                elapsed_ms=_elapsed_ms(started),
            )
        )


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_wigolo_base_url")
    return str(value).rstrip("/")


def _read_token(path: Path) -> str:
    if not path.is_file():
        raise ValueError("wigolo_token_file_missing")
    token = path.read_text(encoding="utf-8").strip()
    if not token or len(token) > 4096:
        raise ValueError("invalid_wigolo_token")
    return token


def _public_urls(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        url = str(item or "").strip()
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and url not in result
        ):
            result.append(url)
    return tuple(result)


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:1000] or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
