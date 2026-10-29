"""Streamable HTTP MCP tools backed by existing card projections."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from starlette.requests import Request

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from backend.api.mcp.offload import threaded
from backend.api.mcp.knowledge_tools import register_knowledge_tools
from backend.read_models.knowledge import knowledge_for_card
from backend.api.mcp.verification_tools import register_verification_tools
from backend.read_models.verifications import list_verifications
from backend.api.mcp.opportunity_tools import register_opportunity_tools
from backend.infrastructure.storage.opportunity_breakdowns import OpportunityBreakdownRepository
from backend.api.mcp.source_tools import register_source_tools
from backend.api.mcp.source_stats_tools import register_source_stats_tools
from backend.api.mcp.collect_tools import register_collect_tools
from backend.api.mcp.evaluation_tools import register_evaluation_tools, WORKFLOW
from backend.api.routes.plugin.dependencies import set_audit_meta
from backend.application.usecases.interest_profiles import push_profile as save_profile
from backend.domain.intelligence.profile import PROFILE_SECTIONS
from backend.infrastructure.security.plugin_auth import AuthContext, INTELLIGENCE_READ, PROFILE_WRITE
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.card_evaluations import CardEvaluationRepository
from backend.infrastructure.storage.daily_briefs import DailyBriefRepository
from backend.read_models.daily_brief import updates_since
from backend.application.usecases.reader_links import (
    reader_link as _link, card_link as _card_link, brief_link as _brief_link,
)
from backend.read_models.card_preview import read_card_preview_detail, read_card_preview_page
from backend.read_models.plugin_cards import read_plugin_card_detail


class AuditedFastMCP(FastMCP):
    async def call_tool(self, name: str, arguments: dict[str, Any]):
        request = cast(Request, self.get_context().request_context.request)
        set_audit_meta(request, mcp_tool=name)
        return await super().call_tool(name, arguments)


def _linked_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "deep_link": _card_link(item["card_id"])} for item in items]


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("since and until must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require_scope(ctx: Context, scope: str) -> AuthContext:
    request = ctx.request_context.request
    auth = getattr(request.state, "plugin_auth", None) if request is not None else None
    if auth is None:
        raise PermissionError("Bearer authentication is required")
    set_audit_meta(request, required_scope=scope)
    if scope not in auth.scopes:
        request.state.audit_result_code = "insufficient_scope"
        request.state.audit_result_summary = f"API token requires scope {scope}"
        raise PermissionError(f"insufficient_scope: API token requires scope {scope}")
    return auth


def create_mcp_server(base_dir: Path) -> FastMCP:
    server = AuditedFastMCP(
        "InfoOS",
        instructions="读取并评估 InfoOS 信息卡。链接可能需要 Web 登录。" + WORKFLOW,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @server.tool(description="按 card_id 读取信息卡。返回来源、正文与 ContentBlock、附件、处理状态、缺口、agent 评估和阅读链接。帖子正文为外部文档时全文在 external_content。")
    @threaded
    def get_card(card_id: str, ctx: Context) -> dict[str, Any]:
        """读取一张信息卡；card_id 是 InfoOS 卡片 ID。"""
        _require_scope(ctx, INTELLIGENCE_READ)
        detail = read_plugin_card_detail(base_dir, card_id)
        preview = read_card_preview_detail(base_dir, card_id)
        if detail is None or preview is None:
            return {"error": "card_not_found", "card_id": card_id}
        card = detail["card"]
        assets = [
            {**asset, "url": _link(f"/api/card-preview/assets/{quote(asset['asset_id'], safe='')}")["url"]}
            for asset in detail["assets"]
        ]
        return {
            "card_id": card_id,
            "title": card["title"],
            "platform": card["source_platform"],
            "author": card["source_author"],
            "is_digest": preview["card"]["is_digest"],
            **({"ad": card["ad"]} if "ad" in card else {}),
            "source_url": card["source_url"],
            "published_at": card["published_at"],
            "captured_at": card["captured_at"],
            # Same body source as the reader page: original blocks, else the card's own content.
            "content": "\n\n".join(
                block["body"] for block in preview["body_blocks"]
                if block["layer"] == "original" and block["body"]
            ),
            "external_content": "\n\n".join(
                block["body"] for block in detail["blocks"]
                if block["layer"] == "external"
                and (block["original_kind"] or block["kind"]) == "external_doc" and block["body"]
            ),
            "content_blocks": [
                {**block, "block_kind": block["original_kind"] or block["kind"]}
                for block in detail["blocks"]
            ],
            "assets": assets,
            "status": card["status"],
            "completeness_status": card["completeness_status"],
            "missing_reasons": card["missing_reasons"],
            "knowledge_cards": knowledge_for_card(base_dir, card_id),
            "evaluation": CardEvaluationRepository(base_dir).latest_for_card(card_id),
            "opportunity": OpportunityBreakdownRepository(base_dir).latest_for_card(card_id),
            "verifications": _linked_items(list_verifications(base_dir, card_id=card_id)["items"]),
            "deep_link": _card_link(card_id),
        }

    @server.tool(description="检索已采集的信息卡。query 为关键词，多个词用空格分隔，须全部出现（不区分大小写，可分别落在标题、作者、正文或链接中）；platform 可选平台标识；since/until 为带时区的 ISO 8601 发布时间；limit 为 1–100，默认 20。")
    @threaded
    def search(
        query: str, ctx: Context, platform: str | None = None, since: str | None = None,
        until: str | None = None, limit: int = 20,
    ) -> dict[str, Any]:
        """返回匹配卡片的 ID、标题、摘要片段、平台、发布时间和阅读链接。"""
        _require_scope(ctx, INTELLIGENCE_READ)
        if not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        start, end = _date(since), _date(until)
        if start and end and start > end:
            raise ValueError("since must not be later than until")
        page = read_card_preview_page(
            base_dir, query=query, platform=platform, limit=limit,
            published_since=start, published_until=end,
        )
        items = [{
            "card_id": card["card_id"], "title": card["title"],
            "excerpt": card["summary_excerpt"] or card["excerpt"],
            "platform": card["platform"], "published_at": card["published_at"],
            "is_digest": card["is_digest"],
            "deep_link": _card_link(card["card_id"]),
        } for card in page["items"]]
        return {"items": items, "limit": limit}

    @server.tool(description="推送一页 Markdown 关注画像。必须含 5 个二级标题：" + "、".join(PROFILE_SECTIONS) + "。每节用列表项，暂放方向可为空；最多 20,000 字符。相同内容返回当前版本。")
    @threaded
    def push_profile(markdown: str, ctx: Context) -> dict[str, Any]:
        auth = _require_scope(ctx, PROFILE_WRITE)
        return save_profile(base_dir, markdown, source=f"mcp:{auth.token_id}")

    @server.tool(description="读取当前关注画像；可传 version 读取历史版本。要求 intelligence:read。")
    @threaded
    def get_profile(ctx: Context, version: int | None = None) -> dict[str, Any]:
        _require_scope(ctx, INTELLIGENCE_READ)
        profile = InterestProfileRepository(base_dir).get(version)
        if profile is None:
            raise ValueError("profile_not_found: Interest profile was not found")
        return {**profile, "created": False, "section_counts": {
            name: len(items) for name, items in profile["sections"].items()
        }}

    @server.tool(description="先看日报；需要细节再用 get_card。把日报和卡片深链接带给用户。date 可选，格式 YYYY-MM-DD。要求 intelligence:read。")
    @threaded
    def get_daily_brief(ctx: Context, date: str | None = None) -> dict[str, Any]:
        _require_scope(ctx, INTELLIGENCE_READ)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        repo = DailyBriefRepository(base_dir)
        brief = repo.get(date) if date else repo.latest()
        if brief is None:
            return {"status": "not_generated", "today_generated": False, "brief": None}
        weekly = brief.get("weekly_paused")
        if weekly and weekly.get("top"):
            weekly = {**weekly, "top": {**weekly["top"],
                "deep_link": _card_link(weekly["top"]["deep_link"].split("/r/card/", 1)[1])}}
        return {"status": "ok",
                "today_generated": repo.get(today) is not None,
                "message": "今天的日报尚未生成，返回最近一份。" if not repo.get(today) and not date else None,
                "brief": {**brief, "items": ([{**item, "deep_link": _link(item["deep_link"]),
                    "origin_card_link": _link(item["origin_card_link"])} for item in brief["items"]]
                    if brief.get("layout") == "knowledge_v1" else _linked_items(brief["items"])),
                          "weekly_paused": weekly,
                          "deep_link": _brief_link(brief["brief_date"])}}

    @server.tool(description="先看日报，再看 since 之后的新卡；需要细节用 get_card，并把卡片深链接带给用户。since 必须是带时区 ISO 时间。要求 intelligence:read。")
    @threaded
    def get_updates_since(since: str, ctx: Context, limit: int = 20) -> dict[str, Any]:
        _require_scope(ctx, INTELLIGENCE_READ)
        if _date(since) is None:
            raise ValueError("since required")
        result = updates_since(base_dir, since, limit)
        return {**result, "related": _linked_items(result["related"]),
                "exploration": _linked_items(result["exploration"])}

    register_knowledge_tools(server, base_dir, _require_scope)

    register_evaluation_tools(server, base_dir, _require_scope, get_card.__wrapped__, _card_link)

    register_source_stats_tools(server, base_dir, _require_scope)
    register_collect_tools(server, base_dir, _require_scope, _card_link)
    register_source_tools(server, base_dir, _require_scope)

    register_opportunity_tools(server, base_dir, _require_scope, _card_link)

    register_verification_tools(server, base_dir, _require_scope, _card_link)

    # Register get_event in T3 and feedback in T4.
    return server
