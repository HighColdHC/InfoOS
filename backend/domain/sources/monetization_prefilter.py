"""Versioned broad-recall rules; no I/O or model-based judgement."""
from __future__ import annotations

from typing import Any

RULES_VERSION = "prefilter_v5"
TOKENS = {
    "contact": ("卫星", "🛰", "vx", "wx", "v", "V", "微信", "薇", "威", "✉️", "📧", "邮箱", "私信", "私我", "私聊", "私戳", "私v", "私滴", "戳", "滴滴"),
    "commercial_intent": ("合作", "商务", "咨询", "带货", "橱窗", "同款", "代理", "加盟", "接单", "定制", "课程", "学员", "陪跑", "训练营", "社群", "粉丝群", "进群"),
    "redirect": ("🌍", "🌏", "全网同名", "：", "👇", "⬇️", "置顶", "看主页"),
    "account_reference": ("@", "小号", "大号"),
    "comment_demand": ("求教程", "求链接", "求分享", "怎么做", "怎么弄", "在哪买", "多少钱", "出教程", "蹲"),
}

# Versioned alongside TOKENS; weak evidence never qualifies on its own.
WEAK_TOKENS = ("@", "：", "v", "V", "🌍", "🌏", "👇", "⬇️", "戳")
STRONG_TOKENS = {key: tuple(token for token in values if token not in WEAK_TOKENS)
                 for key, values in TOKENS.items()}

# Separate policy: changes here must not invalidate Douyin FETCH inputs.
XHS_RULES_VERSION = "xhs_prefilter_v4"
XHS_TOKENS = {
    "contact": ("🍠", "绿泡泡", "绿色软件", "vx", "wx", "v❤", "dd", "滴滴", "微信", "邮箱", "私信", "私我", "私聊", "私戳", "私v", "私滴", "戳"),
    "commercial_intent": ("合作", "商务", "店铺", "橱窗", "链接", "群聊", "进群", "粉丝群", "课程", "陪跑", "代理", "同款"),
    "redirect": ("主页", "看简介", "看主页", "置顶", "全网同名", "👇", "⬇️"),
    "account_reference": ("@", "小号", "大号"),
    "comment_demand": ("求链接", "怎么买", "哪里买", "求教程", "求分享", "多少钱", "蹲", "出教程"),
}

XHS_WEAK_TOKENS = ("@", "👇", "⬇️", "戳", "dd")
XHS_STRONG_TOKENS = {key: tuple(token for token in values if token not in XHS_WEAK_TOKENS)
                     for key, values in XHS_TOKENS.items()}


def token_strengths(tokens: dict[str, tuple[str, ...]], weak: tuple[str, ...]) -> dict[str, str]:
    weak_folded = {ascii_fold(token) for token in weak}
    return {ascii_fold(token): "weak" if ascii_fold(token) in weak_folded else "strong"
            for values in tokens.values() for token in values}


def has_strong_hit(hits: list[dict[str, str]]) -> bool:
    return any(hit["strength"] == "strong" for hit in hits)


def xhs_rules(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    metadata = {} if metadata is None else metadata
    if not isinstance(metadata, dict):
        raise ValueError("xiaohongshu home_feed raw_metadata must be an object")
    enabled = metadata.get("monetization_prefilter", False)
    if not isinstance(enabled, bool):
        raise ValueError("monetization_prefilter must be a boolean")
    if not enabled:
        return None
    threshold = metadata.get("prefilter_demand_min_comments", 2)
    if type(threshold) is not int or not 1 <= threshold <= 20:
        raise ValueError("prefilter_demand_min_comments must be an integer in 1-20")
    return {"rules_version": XHS_RULES_VERSION,
            "token_strengths": token_strengths(XHS_TOKENS, XHS_WEAK_TOKENS),
            "tokens": {key: list(dict.fromkeys(ascii_fold(v) for v in values))
                       for key, values in XHS_TOKENS.items()},
            "demand_min_comments": threshold, "coverage": ["title", "content", "tags"],
            "profile_coverage": ["author_signature", "author_pinned", "author_group_entry"],
            "max_profiles": 20, "profile_gap_seconds": 3, "comments": "first_screen"}


def ascii_fold(value: str) -> str:
    return value.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def xhs_text_hits(text: str, field: str, rules: dict[str, Any], *, comments: bool = False) -> list[dict[str, str]]:
    tokens = dict.fromkeys(ascii_fold(token) for category, values in rules["tokens"].items()
                           if (category == "comment_demand") == comments for token in values)
    normalized = ascii_fold(text)
    return [{"field": field, "token": token, "strength": rules["token_strengths"][token]}
            for token in tokens if token and token in normalized]


def adapter_rules(options: dict[str, Any]) -> dict[str, Any]:
    return {"rules_version": RULES_VERSION,
            "token_strengths": token_strengths(TOKENS, WEAK_TOKENS),
            "tokens": {key: list(value) for key, value in TOKENS.items()},
            "comment_min_count": options["prefilter_comment_min_count"],
            "demand_min_comments": options["prefilter_demand_min_comments"]}


def run_evidence(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Count observations before live/cursor filtering; retain only light misses."""
    summary = {"rules_version": RULES_VERSION, "checked": 0, "matched": 0,
               "missed": 0, "unknown": 0, "skipped": 0, "token_hits": {},
               "mention_targets": 0, "mention_profiles_ok": 0, "mention_hit_items": 0,
               "mention_pinned_checked": 0, "mention_pinned_ok": 0, "mention_pinned_hit_items": 0}
    misses = []
    for record in records:
        result = record.get("prefilter") or {}
        status = result.get("status", "unknown")
        if status not in {"matched", "missed", "unknown", "skipped"}:
            status = "unknown"
        summary[status] += 1
        summary["checked"] += status != "skipped"
        mentions = result.get("mentions", [])
        summary["mention_targets"] += len(mentions)
        summary["mention_profiles_ok"] += sum(mention.get("profile_ok") is True for mention in mentions)
        summary["mention_pinned_checked"] += sum(mention.get("pinned_checked") is True for mention in mentions)
        summary["mention_pinned_ok"] += sum(mention.get("pinned_ok") is True for mention in mentions)
        summary["mention_pinned_hit_items"] += any(
            hit["field"] == "mention_pinned" for hit in result.get("hits", []))
        summary["mention_hit_items"] += any(
            hit["field"] in {"mention_signature", "mention_fans_group"} for hit in result.get("hits", []))
        # Per-token observation count, not occurrences or number of comments.
        for token in {hit["token"] for hit in result.get("hits", [])}:
            summary["token_hits"][token] = summary["token_hits"].get(token, 0) + 1
        if status == "missed":
            misses.append({
                "aweme_id": record.get("aweme_id", ""), "url": record.get("url", ""),
                "author_sec_uid": str(record.get("author_url") or "").rsplit("/", 1)[-1],
                "author": record.get("author", ""), "author_signature": record.get("author_signature", ""),
                "title": record.get("title", ""),
                **{key: record.get(key, 0) for key in ("digg_count", "comment_count", "share_count", "collect_count")},
                "hits": result.get("hits", []), "rules_version": result.get("rules_version", RULES_VERSION),
                "mention_sec_uids": [mention["sec_uid"] for mention in mentions],
            })
    return {"prefilter_misses": misses, "prefilter_summary": summary}
