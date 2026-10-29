"""ZSXQ article discovery and read evidence; no browser or storage effects."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

ARTICLE_POLICY_VERSION = "zsxq-article-v1"
_ARTICLE_PATH = re.compile(r"/(?:inline_form/)?id_([A-Za-z0-9_-]+)\.html")


def normalize_article_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        match = _ARTICLE_PATH.fullmatch(parsed.path)
        if (parsed.scheme == "https" and parsed.hostname == "articles.zsxq.com"
                and not parsed.username and not parsed.password and parsed.port in {None, 443} and match):
            return f"https://articles.zsxq.com/id_{match[1]}.html"
    except ValueError:
        return ""
    return ""


def article_url(payload: Any) -> str:
    """Read current metadata first, then historical nested link containers."""
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("article_url")
    if isinstance(direct, str) and (url := normalize_article_url(direct)):
        return url
    for key in ("raw_metadata", "raw_payload", "opencli_record"):
        if url := article_url(payload.get(key)):
            return url
    links = payload.get("links")
    if isinstance(links, list):
        for value in links:
            if isinstance(value, str) and (url := normalize_article_url(value)):
                return url
    return ""


def article_evidence(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    overlay = payload.get("zsxq_article_read")
    if isinstance(overlay, dict):
        return overlay
    for key in ("raw_metadata", "raw_payload", "opencli_record"):
        if found := article_evidence(payload.get(key)):
            return found
    return {}


def article_post(payload: Any) -> str:
    """Card evidence is compact; the article body is owned by raw metadata."""
    if not isinstance(payload, dict):
        return ""
    evidence = payload.get("zsxq_article_read")
    if isinstance(evidence, dict) and isinstance(evidence.get("original_content"), str):
        return evidence["original_content"]
    for key in ("raw_metadata", "raw_payload"):
        if post := article_post(payload.get(key)):
            return post
    return ""


def article_gap(card: dict[str, Any]) -> str | None:
    if card["source_platform"] != "zsxq" or not article_url(card):
        return None
    status = article_evidence(card).get("status", "pending")
    return None if status == "fetched" else f"zsxq_article_{status}"
