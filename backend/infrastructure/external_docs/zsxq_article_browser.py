"""Bounded article-page reads through the dedicated InfoOS OpenCLI profile."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.domain.failures import OpenCLISiteLeaseTimeout
from backend.domain.zsxq_article import normalize_article_url
from backend.infrastructure.external_docs.feishu_browser import _adapter_record, _bounded_media_urls, _first_json_value
from backend.infrastructure.opencli.commands import run_opencli
from backend.infrastructure.opencli.tab_lease import current_opencli_context_id, opencli_tab_lease


@dataclass(frozen=True)
class ZsxqArticleResult:
    status: str
    url: str
    title: str = ""
    body: str = ""
    complete: bool = False
    images: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def fetch_zsxq_article(url: str, *, timeout: int = 65) -> ZsxqArticleResult:
    source_url = normalize_article_url(url)
    if not source_url:
        return ZsxqArticleResult("failed", url, diagnostics={"reason": "url_not_allowed"})
    try:
        with opencli_tab_lease("zsxq", timeout_seconds=30, close_tabs=False):
            result = run_opencli([
                "--profile", current_opencli_context_id(), "zsxq", "article", source_url,
                "--format", "json", "--window", "background",
                "--site-session", "ephemeral", "--keep-tab", "false",
            ], timeout=timeout)
    except OpenCLISiteLeaseTimeout:
        raise
    except TimeoutError as exc:
        raise OpenCLISiteLeaseTimeout("zsxq") from exc
    except Exception as exc:  # noqa: BLE001 - OpenCLI process boundary records structured browser failures for task retry
        return ZsxqArticleResult("failed", url, diagnostics={"error_class": type(exc).__name__})
    if result.returncode:
        return ZsxqArticleResult("failed", url, diagnostics={"returncode": result.returncode})
    payload = _adapter_record(_first_json_value(result.stdout))
    if payload is None or not normalize_article_url(str(payload.get("url", ""))):
        return ZsxqArticleResult("failed", url, diagnostics={"reason": "invalid_output_or_redirect"})
    status = payload.get("status")
    if status not in {"fetched", "login_required", "empty"}:
        return ZsxqArticleResult("failed", url, diagnostics={"reason": "invalid_status"})
    body = str(payload.get("body") or "").strip()
    return ZsxqArticleResult(
        status="empty" if status == "fetched" and not body else status,
        url=source_url, title=str(payload.get("title") or "").strip(), body=body[:120000],
        complete=payload.get("complete") is True and len(body) < 120000,
        images=_bounded_media_urls(payload.get("images")), links=_bounded_media_urls(payload.get("links")),
    )
