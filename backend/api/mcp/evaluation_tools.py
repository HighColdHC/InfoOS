"""External-agent evaluation workflow exposed through scope-checked MCP tools."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from mcp.server.fastmcp import Context, FastMCP

from backend.api.mcp.offload import threaded
from backend.application.usecases.submit_evaluations import submit_evaluations as save_evaluations
from backend.domain.intelligence.rubric import ENUM_MEANINGS, QUALITY_THRESHOLD, RUBRIC_TEXT, RUBRIC_VERSION
from backend.infrastructure.security.plugin_auth import EVALUATION_WRITE, INTELLIGENCE_READ
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.read_models.new_cards import list_new_cards as read_new_cards

WORKFLOW = ("先调用 get_evaluation_rubric 和 get_profile，再用 list_new_cards 取未评估卡，"
            "get_cards 分批读取（最多20张）；truncated 为 true 且全文对评估重要时用 get_card 读取，submit_evaluations 分批写回（最多20条）。给用户看的内容须附深链接。")


def _bounded_card(card: dict[str, Any]) -> dict[str, Any]:
    original = card.get("content", "")
    blocks = card.get("content_blocks", [])
    external = card.get("external_content", "")
    content = original[:4000]
    external_content = external[:4000 - len(content)]
    remaining = 4000 - len(content) - len(external_content)
    truncated = len(content) < len(original) or len(external_content) < len(external)
    bounded = [{**b, "segments": []} for b in blocks]
    # Budget primary text before derived text, retaining the original block order.
    order = sorted(range(len(blocks)), key=lambda i: blocks[i]["layer"] not in {"original", "external"})
    for index in order:
        block, item = blocks[index], bounded[index]
        if block["segments"]:
            item["segments_omitted"] = True
        body = block["body"]
        if block["block_kind"] == "opportunity_breakdown" and card["opportunity"] is not None:
            item.update(body="", body_in_opportunity=True)
        elif block["layer"] == "original" and body and body in original:
            item.update(body="", body_in_content=True)
        elif block["layer"] == "external" and block["block_kind"] == "external_doc" and body and body in external:
            item.update(body="", body_in_external_content=True)
        else:
            item["body"] = body[:remaining]
            remaining -= len(item["body"])
            truncated |= len(item["body"]) < len(body)
    return {**card, "content": content, "external_content": external_content,
            "content_blocks": bounded, "truncated": truncated,
            "full_length": len(original) + len(external),
            **({"truncated_hint": "调用 get_card 读取全文"} if truncated else {})}


def register_evaluation_tools(server: FastMCP, base_dir: Path, require_scope: Callable,
                              get_card: Callable, card_link: Callable) -> None:
    @server.tool(description="读取 InfoOS 单一评分标准、版本、阈值、枚举含义与当前画像版本。须同时调用 get_profile。" + WORKFLOW)
    @threaded
    def get_evaluation_rubric(ctx: Context) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        profile = InterestProfileRepository(base_dir).get()
        return {"rubric_version": RUBRIC_VERSION, "rubric": RUBRIC_TEXT, "quality_threshold": QUALITY_THRESHOLD,
                "enums": ENUM_MEANINGS, "profile_version": profile["profile_version"] if profile else None,
                "workflow": WORKFLOW}

    @server.tool(description="按首次进入时间 [since, until) 升序读取新卡；带时区 ISO 时间，limit 1–100（默认50），cursor 续页，only_unevaluated 默认true（当前画像和rubric）。" + WORKFLOW)
    @threaded
    def list_new_cards(since: str, ctx: Context, until: str | None = None, limit: int = 50,
                       cursor: str | None = None, only_unevaluated: bool = True,
                       platform: str | None = None) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        return read_new_cards(base_dir, since, until, limit, cursor, only_unevaluated, platform)

    @server.tool(description="一次读取1–20张卡，内容同 get_card；opportunity结构化拆解不计入正文预算，其block正文以body_in_opportunity指向该字段；正文与其余block文本合计最多4000字符且不重复计费；原文和外部文档正文优先，分别在content与external_content给出（body_in_content/body_in_external_content）；segments省略，时间戳用get_card；truncated标明截断，full_length为原文与外部正文完整字符数，truncated_hint提示单卡读取；缺失卡逐条返回错误。" + WORKFLOW)
    @threaded
    def get_cards(card_ids: list[str], ctx: Context) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        if not 1 <= len(card_ids) <= 20:
            raise ValueError("card_ids must contain 1–20 IDs")
        return {"items": [_bounded_card(get_card(card_id, ctx)) for card_id in card_ids]}

    @server.tool(description="要求 evaluation:write。evaluator 必填（1–200字符，如codex:gpt-6.1-sol），items 1–20条，每条含card_id和rubric要求的全部字段。逐条返回accepted/rejected及原因；重复唯一键覆盖，使用当前画像版本。返回normalized与warnings，摘要与关系说明各不超过500字符。" + WORKFLOW)
    @threaded
    def submit_evaluations(evaluator: str, items: list[dict[str, Any]], ctx: Context) -> dict[str, Any]:
        auth = require_scope(ctx, EVALUATION_WRITE)
        result = save_evaluations(base_dir, evaluator, items, token_id=auth.token_id)
        for item in result["results"]:
            if item.get("card_id"):
                item["deep_link"] = card_link(item["card_id"])
        return result
