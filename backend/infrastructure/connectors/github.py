"""Public GitHub single-link acquisition and shared repository normalization."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from backend.domain.captures import capture_media_urls, sanitize_readable_html
from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps
from backend.domain.timestamps import utc_now_micro_z
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.connectors.github_discussion import extract_discussion, without_form_inputs
from backend.infrastructure.public_http import read_public_url


@dataclass
class GitHubItemConnector:
    connector_id: str
    identifier: str

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        target = _target(self.identifier)
        if target is None:
            raise ValueError("github_capture_unsupported_url")
        kind, repository, suffix = target
        api = f"https://api.github.com/repos/{repository}"
        if kind == "discussion":
            page, final_url = _read(self.identifier, accept="text/html", api=False)
            title, html = extract_discussion(page)
            item = RawItem(
                raw_item_id=final_url, connector_id=self.connector_id,
                source_platform="github", source_type="discussion",
                source_url=final_url, captured_at=utc_now_micro_z(),
                raw_metadata={"title": title},
            )
        elif kind == "repository":
            record = _json(api)
            _required_text(record, "full_name")
            _required_text(record, "html_url")
            item = github_repository_raw_item(self.connector_id, record)
            html, _ = _read(f"{api}/readme", accept="application/vnd.github.html+json")
        else:
            record = _json(f"{api}/{suffix}")
            title = _text(record.get("title")) if kind != "release" else (
                _text(record.get("name")) or _required_text(record, "tag_name")
            )
            if not title:
                raise RuntimeError("github_item_title_missing")
            author_value = record.get("user") if kind != "release" else record.get("author")
            author = author_value if isinstance(author_value, dict) else {}
            item = RawItem(
                raw_item_id=str(record.get("id") or self.identifier),
                connector_id=self.connector_id, source_platform="github", source_type=kind,
                source_url=_required_text(record, "html_url"), captured_at=utc_now_micro_z(),
                raw_author=RawAuthor(name=_text(author.get("login")) or None, url=author.get("html_url")),
                raw_timestamps=RawTimestamps(
                    published_at=_text(record.get("published_at") or record.get("created_at")) or None,
                    updated_at=_text(record.get("updated_at")) or None,
                ),
                raw_metadata={"title": title},
            )
            # Releases expose markdown; full issue responses also provide rendered HTML.
            html = _text(record.get("body_html")) or f"<pre>{escape(_text(record.get('body')))}</pre>"
        html = without_form_inputs(html)
        readable_html, text = sanitize_readable_html(html, base_url=item.source_url)
        if not text:
            raise RuntimeError("github_item_body_empty")
        item = item.model_copy(update={
            "raw_text": text,
            "raw_metadata": {
                **item.raw_metadata,
                "capture_adapter": "github:item",
                "github_item_type": kind,
                "sanitized_readable_html": readable_html,
                "fetch_final_url": item.source_url,
                **_github_media_urls(html, base_url=item.source_url),
            },
        })
        items = [] if since_cursor == item.source_url else [item]
        return FetchResult(items=items, next_cursor=item.source_url, visible_count=1, new_count=len(items))


def _github_media_urls(html: str, *, base_url: str) -> dict[str, list[str]]:
    media = capture_media_urls(html, base_url=base_url)
    if "images" in media:
        media["images"] = [url for url in media["images"] if not _is_badge_or_svg(url)]
    return media


def _is_badge_or_svg(url: str) -> bool:
    parsed = urlsplit(url)
    # GitHub camo encodes the original image URL in its last path segment.
    if parsed.hostname == "camo.githubusercontent.com":
        try:
            original = bytes.fromhex(parsed.path.rsplit("/", 1)[-1]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        parsed = urlsplit(original)
    return parsed.hostname in {"img.shields.io", "shields.io", "badge.fury.io"} or parsed.path.lower().endswith(".svg")


def supported_github_target(url: str) -> bool:
    return _target(url) is not None


def _target(url: str) -> tuple[str, str, str] | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in {".", ".."} for part in parts[:2]):
        return None
    repository = "/".join(parts[:2]).removesuffix(".git")
    if len(parts) == 2 and parts[0] != "orgs":
        return "repository", repository, ""
    if len(parts) == 4 and parts[2] in {"issues", "pull", "discussions"} and parts[3].isdigit():
        kind = {"issues": "issue", "pull": "pull_request", "discussions": "discussion"}[parts[2]]
        return kind, repository, f"issues/{parts[3]}"
    if len(parts) > 4 and parts[2:4] == ["releases", "tag"]:
        tag = quote(unquote("/".join(parts[4:])), safe="")
        return "release", repository, f"releases/tags/{tag}"
    if len(parts) == 4 and parts[2:] == ["releases", "latest"]:
        return "release", repository, "releases/latest"
    return None


def _read(url: str, *, accept: str, api: bool = True) -> tuple[str, str]:
    result = read_public_url(
        url, allowed_hosts=frozenset({"api.github.com"} if api else {"github.com", "www.github.com"}),
        headers={"Accept": accept, "User-Agent": "InfoOS/0.1 GitHub capture", "X-GitHub-Api-Version": "2022-11-28"},
        timeout=(3.05, 20), max_bytes=5 * 1024 * 1024,
    )
    if result is None:
        raise RuntimeError("github_capture_unavailable")
    content, opened = result
    return content.decode("utf-8"), opened.final_url


def _json(url: str) -> dict[str, Any]:
    content, _ = _read(url, accept="application/vnd.github.full+json")
    record = json.loads(content)
    if not isinstance(record, dict):
        raise ValueError("github_capture_invalid_response")
    return record


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _required_text(record: dict[str, Any], key: str) -> str:
    value = _text(record.get(key))
    if not value:
        raise ValueError(f"github_capture_missing_{key}")
    return value


def github_repository_raw_item(connector_id: str, record: dict[str, Any]) -> RawItem:
    url = str(record.get("html_url") or "")
    repo_id = str(record.get("id") or url or hashlib.sha1(
        json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20])
    owner_value = record.get("owner")
    owner = owner_value if isinstance(owner_value, dict) else {}
    published = str(record.get("updated_at") or record.get("created_at") or "") or None
    name = str(record.get("full_name") or record.get("name") or repo_id)
    description = str(record.get("description") or "")
    return RawItem(
        raw_item_id=repo_id,
        connector_id=connector_id,
        source_platform="github",
        source_type="repository",
        source_url=url,
        captured_at=published or utc_now_micro_z(),
        raw_text="\n\n".join(value for value in (name, description) if value),
        raw_author=RawAuthor(name=str(owner.get("login") or "") or None, url=owner.get("html_url")),
        raw_timestamps=RawTimestamps(published_at=published),
        raw_metadata={
            "title": name,
            "stars": record.get("stargazers_count"),
            "language": record.get("language"),
            "topics": record.get("topics") or [],
            "github_record": record,
        },
    )
