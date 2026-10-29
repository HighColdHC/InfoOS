"""InfoOS bounded HTTP implementation of the public-document contract."""
from __future__ import annotations

from hashlib import sha256
from html.parser import HTMLParser
import time
from urllib.parse import urljoin, urlsplit

from urllib3 import exceptions as urllib3_exceptions

from backend.domain.captures import capture_media_urls, sanitize_readable_html
from backend.domain.public_documents import (
    PublicDocument,
    PublicDocumentFailure,
    PublicDocumentOutcome,
    PublicDocumentRequest,
)
from backend.infrastructure.public_http import read_public_url


class HttpPublicDocumentProvider:
    name = "infoos_http"

    def fetch(self, request: PublicDocumentRequest) -> PublicDocumentOutcome:
        started = time.monotonic()
        try:
            opened = read_public_url(
                request.url,
                headers=request.headers,
                timeout=(3.05, request.timeout_seconds),
                max_bytes=request.max_bytes,
            )
            if opened is None:
                return self._failure("unsafe_or_unavailable", started)
            content, response = opened
            document = content.decode("utf-8", errors="replace")
            readable_html, text = sanitize_readable_html(
                document,
                base_url=response.final_url,
            )
            if not text:
                return self._failure("empty_content", started)
            parsed = _DocumentMetadataParser(response.final_url)
            parsed.feed(document)
            parsed.close()
            media = capture_media_urls(document, base_url=response.final_url)
            status_code = response.response.status_code
            return PublicDocumentOutcome(
                document=PublicDocument(
                    provider=self.name,
                    final_url=response.final_url,
                    title=parsed.title,
                    content=text,
                    readable_html=readable_html or None,
                    links=tuple(parsed.links),
                    images=tuple(media.get("images", ())),
                    videos=tuple(media.get("videos", ())),
                    fetch_tier="http",
                    cache_hit=None,
                    status_code=status_code,
                    elapsed_ms=_elapsed_ms(started),
                    response_bytes=len(content),
                    content_hash=sha256(text.encode("utf-8")).hexdigest(),
                )
            )
        except TimeoutError:
            return self._failure("timeout", started)
        except urllib3_exceptions.HTTPError:
            return self._failure("http_error", started)
        except Exception:  # noqa: BLE001 - External HTTP and HTML parser failures must become structured provider failures
            return self._failure("provider_error", started)

    def _failure(self, failure_class: str, started: float) -> PublicDocumentOutcome:
        return PublicDocumentOutcome(
            failure=PublicDocumentFailure(
                provider=self.name,
                failure_class=failure_class,
                elapsed_ms=_elapsed_ms(started),
            )
        )


class _DocumentMetadataParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title: str | None = None
        self.links: list[str] = []
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized == "title":
            self._in_title = True
        if normalized != "a":
            return
        values = {str(key).lower(): value for key, value in attrs}
        href = str(values.get("href") or "").strip()
        if not href:
            return
        resolved = urljoin(self.base_url, href)
        parsed = urlsplit(resolved)
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and resolved not in self.links
        ):
            self.links.append(resolved)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
            value = " ".join("".join(self._title_parts).split()).strip()
            self.title = value[:1000] or None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
