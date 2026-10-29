from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from backend.infrastructure.opencli.commands import pin_adapter_lifecycle
from backend.infrastructure.opencli.runtime import resolve_opencli_executable
from backend.infrastructure.opencli.tab_lease import opencli_tab_lease
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository


MAX_PLATFORM_COMMENTS = 50


def enrich_platform_comments(
    card_id: str, base_dir: Path | str, card: dict[str, Any] | None = None,
) -> int:
    if card is None:
        card = CardRepository(base_dir).get_card(card_id)
    if not card:
        return 0
    platform = card["source_platform"]
    comments: list[dict[str, Any]] = []
    if platform == "xiaohongshu":
        comments = _fetch_xiaohongshu_comments(card["source_url"])
    elif platform == "douyin":
        from backend.application.enrichment.card_source import card_with_source_payload

        source_card = card_with_source_payload(card, base_dir)
        comments = _douyin_comments_from_payload(source_card["raw_payload"])
    if comments:
        _upsert_comments_block(card, base_dir, comments, status="enriched")
        return 1
    if platform in {"xiaohongshu", "douyin", "scys", "zsxq"}:
        _upsert_comments_block(
            card,
            base_dir,
            [],
            status="blocked",
            reason="platform_comments_unavailable",
        )
        return 1
    return 0


def _fetch_xiaohongshu_comments(note_url: str) -> list[dict[str, Any]]:
    if not note_url:
        return []
    command = [resolve_opencli_executable("opencli"), *pin_adapter_lifecycle([
        "xiaohongshu",
        "comments",
        note_url,
        "--limit",
        str(MAX_PLATFORM_COMMENTS),
        "--format",
        "json",
        "--window",
        "background",
    ], site_session="persistent", keep_tab=False)]
    try:
        with opencli_tab_lease("xiaohongshu", close_tabs=False):
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return _normalize_comment_list(payload)


def _douyin_comments_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    candidates = payload.get("top_comments")
    if candidates is None:
        nested = payload.get("raw_payload")
        if isinstance(nested, dict):
            candidates = nested.get("top_comments")
    return _normalize_comment_list(candidates)


def _normalize_comment_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        payload = [line.strip() for line in payload.splitlines() if line.strip()]
    if not isinstance(payload, list):
        return []
    comments: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if isinstance(item, str):
            text = item.strip()
            if text:
                comments.append({"rank": index, "author": "", "text": text, "likes": 0})
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or item.get("comment") or "").strip()
        if not text:
            continue
        comments.append(
            {
                "rank": _to_int(item.get("rank"), default=index),
                "author": str(item.get("author") or item.get("nickname") or item.get("user") or ""),
                "text": text,
                "likes": _to_int(item.get("likes", item.get("digg_count", item.get("like_count", 0)))),
                "time": str(item.get("time") or item.get("created_at") or ""),
            }
        )
    comments.sort(key=lambda row: row["likes"], reverse=True)
    return comments[:MAX_PLATFORM_COMMENTS]


def _upsert_comments_block(
    card: dict[str, Any],
    base_dir: Path | str,
    comments: list[dict[str, Any]],
    *,
    status: str,
    reason: str | None = None,
) -> None:
    repo = CardContentBlockRepository(base_dir)
    card_id = card["card_id"]
    raw_payload: dict[str, Any] = {"comments": comments}
    if reason:
        raw_payload["reason"] = reason
    repo.upsert_block(
        {
            "block_id": f"block_{card_id}_platform_comments",
            "card_id": card_id,
            "position": _next_block_position(repo, card_id),
            "layer": "context",
            "block_kind": "platform_comments",
            "status": status,
            "body": _format_comments_body(comments) if comments else "",
            "segments": [],
            "source_url": card["source_url"],
            "provenance": f"{card['source_platform']} platform comments",
            "raw_payload": raw_payload,
        }
    )


def _format_comments_body(comments: list[dict[str, Any]]) -> str:
    lines = []
    for index, comment in enumerate(comments, start=1):
        author = f"{comment['author']}: " if comment["author"] else ""
        lines.append(f"{index}. {author}{comment['text']} (likes: {comment['likes']})")
    return "\n".join(lines)


def _next_block_position(repo: CardContentBlockRepository, card_id: str) -> int:
    return max((block["position"] for block in repo.list_for_card(card_id)), default=-1) + 1


def _to_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default
