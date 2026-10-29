"""Pure Feishu evidence and retained-post helpers shared by delivery and repair."""
from __future__ import annotations

from typing import Any

INCOMPLETE_FEISHU_STATUSES = {"partial", "login_required", "empty"}


def compact_feishu_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep document status facts, never source records or media payloads."""
    return {key: value for key, value in payload.items()
            if (key.startswith("content_source") or key in {"original_content", "reason", "blocks", "reached_end"}
                or key in {"url", "source_url", "document_url", "resolved_url", "original_url"})
            and isinstance(value, (str, int, float, bool, type(None)))}


def feishu_metadata(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    overlay = payload.get("external_doc_read")
    if isinstance(overlay, dict):
        return overlay
    if payload.get("content_source") == "feishu":
        return payload
    for key in ("raw_metadata", "raw_payload", "opencli_record"):
        found = feishu_metadata(payload.get(key))
        if found:
            return found
    return {}


def original_post(payload: Any) -> str:
    from backend.domain.zsxq_article import article_post

    if post := article_post(payload):
        return post
    evidence = feishu_metadata(payload)
    if "original_content" in evidence:
        return str(evidence.get("original_content") or "").strip()
    # The old zsxq adapter retained getTopicText(topic) in the record's title,
    # even when full_text/content were replaced with the Feishu first screen.
    if isinstance(payload, dict):
        record = payload.get("opencli_record")
        if isinstance(record, dict) and record.get("content_source") == "feishu":
            return str(record.get("original_content", record.get("title")) or "").strip()
        for key in ("raw_metadata", "raw_payload"):
            text = original_post(payload.get(key))
            if text:
                return text
    return ""


def feishu_gap(payload: Any) -> str | None:
    status = str(feishu_metadata(payload).get("content_source_status") or "")
    return f"external_doc_{status}" if status in INCOMPLETE_FEISHU_STATUSES else None


def suspect_feishu_body(body: str) -> bool:
    tail = body.rstrip()[-200:]
    return any(marker in tail for marker in ("帮助中心", "效率指南", "快捷键", "评论（0）", "评论(0)"))


def combine_post_document(post: str, body: str) -> str:
    post, body = post.strip(), body.strip()
    if not post or body == post or body.startswith(post + "\n"):
        return body
    return f"{post}\n\n{body}" if body else post


def keeps_previous_body(new_length: int, previous_length: int, *, complete: bool) -> bool:
    """Whether a shorter read must not replace what is stored.

    A read that reached the end of the document may be somewhat shorter than a
    previous one that mixed in page chrome; anything else never shrinks content.
    """
    if new_length >= previous_length:
        return False
    return not complete or new_length < previous_length * 0.8
