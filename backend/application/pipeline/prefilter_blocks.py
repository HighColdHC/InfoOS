"""Preserve deterministic screening evidence as independently readable blocks."""
from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.repositories import CardContentBlockRepository


def preserve_prefilter_blocks(base_dir: Path, card_id: str, raw_item: RawItem) -> None:
    metadata = raw_item.raw_metadata
    evidence = metadata.get("prefilter")
    if raw_item.source_platform not in {"douyin", "xiaohongshu"} or not isinstance(evidence, dict):
        return
    signature = metadata.get("author_signature", "")
    profile_body = str(signature) if signature else (
        "作者简介为空。" if evidence.get("profile_ok") else "作者简介未取得。")
    if raw_item.source_platform == "xiaohongshu":
        if evidence.get("profile_ok") is None:
            profile_body = "作者简介未读取。"
        titles = metadata.get("author_pinned", [])
        profile_body += ("\n置顶笔记：\n" + ("\n".join(titles) or "首屏无置顶笔记。")
                         if evidence.get("pinned_ok") else "\n置顶笔记未取得。")
    mentions = evidence.get("mentions", [])
    if mentions:
        paragraphs = []
        for mention in mentions:
            description = (mention.get("signature") or "简介为空。") if mention.get("profile_ok") else "简介未取得。"
            if mention.get("pinned_ok"):
                pinned = mention.get("pinned", [])
                descriptions = "\n".join(
                    f"{index}. {video.get('desc') or '描述为空。'}" for index, video in enumerate(pinned, 1))
                description += "\n置顶作品：\n" + (descriptions or "首屏无置顶作品。")
            else:
                description += "\n置顶作品未取得。" if mention.get("pinned_checked") else "\n置顶作品未取得（未请求）。"
            paragraphs.append(f"{mention['sec_uid']}：\n{description}")
        profile_body += "\n\n被 @ 账号简介\n\n" + "\n\n".join(paragraphs)
    state = {"matched": "命中", "unknown": "未知，按命中保留", "skipped": "跳过初筛"}.get(
        evidence.get("status"), "未命中")
    hits = "；".join(f"{hit['field']}: {hit['token']}" for hit in evidence.get("hits", [])) or "无"
    body = (f"变现初筛：{state}；规则版本：{evidence.get('rules_version')}。\n"
            f"命中字段与词：{hits}。\n"
            f"资料读取成功：{evidence.get('profile_ok')}；检查评论：{evidence.get('comments_checked')}；"
            f"评论读取成功：{evidence.get('comments_ok')}；需求评论数：{evidence.get('demand_comment_count', 0)}。\n"
            f"仅记录、不单独构成命中的资料信号：{json.dumps(evidence.get('signals', {}), ensure_ascii=False)}。\n"
            "这是确定性宽召回信号，尚未评估实际变现价值。")
    if raw_item.source_platform == "xiaohongshu":
        body += ("\n本版按需检查：标题、正文、话题、作者简介、首屏置顶标题、可见群入口、首屏非作者评论。"
                 "\n结构化 @ 未追踪；群/店内容未进入。未取得信号："
                 + "、".join(evidence.get("unavailable_signals", [])))
    blocks: list[dict[str, Any]] = []
    for position, kind, text, payload in (
        (20, "author_profile", profile_body, {"author_signature": signature,
                                            "profile_ok": evidence.get("profile_ok"), "mentions": mentions,
                                            **({"pinned_titles": metadata.get("author_pinned", []),
                                                "pinned_ok": evidence.get("pinned_ok")}
                                               if raw_item.source_platform == "xiaohongshu" else {})}),
        (21, "screening_evidence", body, {**evidence, "mentions": mentions}),
    ):
        blocks.append({"block_id": f"block_{card_id}_{kind}", "card_id": card_id,
                       "position": position, "layer": "context", "block_kind": kind,
                       "status": "preserved", "body": text, "source_url": raw_item.source_url,
                       "provenance": f"{raw_item.source_platform} home feed monetization prefilter",
                       "raw_payload": payload})
    CardContentBlockRepository(base_dir).upsert_blocks(blocks)
