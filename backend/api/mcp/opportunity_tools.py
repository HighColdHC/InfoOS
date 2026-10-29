"""Scope-checked tools for evidence-based opportunity breakdowns."""
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP

from backend.api.mcp.offload import threaded
from backend.application.usecases.reader_links import opportunity_link
from backend.application.usecases.opportunity_follow_up import set_opportunity_follow_up as save_follow_up
from backend.application.usecases.submit_opportunity_breakdowns import submit_opportunity_breakdowns as save_breakdowns
from backend.domain.intelligence.opportunity import (
    ENUM_MEANINGS, FIELD_CONSTRAINTS, OPPORTUNITY_CANDIDATE_RULES_VERSION,
    OPPORTUNITY_LENS_TEXT, OPPORTUNITY_LENS_VERSION, WORKFLOW,
)
from backend.infrastructure.security.plugin_auth import EVALUATION_WRITE, INTELLIGENCE_READ, VERIFICATION_WRITE
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.read_models.opportunities import list_opportunities as read_opportunities
from backend.read_models.opportunities import list_opportunity_candidates as read_candidates


def _error(exc: ValueError) -> dict[str, Any]:
    return {"error": str(exc), "message": "商机参数无效，请检查时间窗、游标及视角中的字段约束"}


def register_opportunity_tools(server: FastMCP, base_dir: Path, require_scope: Callable, card_link: Callable) -> None:
    def linked(page):
        page["items"] = [{**item, "deep_link": opportunity_link(item["card_id"]), "card_link": card_link(item["card_id"])} for item in page["items"]]
        return page

    @server.tool(description="要求 intelligence:read。先读取商机拆解视角、版本、枚举及字段约束，再读线索卡写回拆解；与通用 rubric_v1 并列。" + WORKFLOW)
    @threaded
    def get_opportunity_lens(ctx: Context) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        profile = InterestProfileRepository(base_dir).get()
        return {"profile_version": profile["profile_version"] if profile else None, "lens_version": OPPORTUNITY_LENS_VERSION, "rules_version": OPPORTUNITY_CANDIDATE_RULES_VERSION,
                "lens": OPPORTUNITY_LENS_TEXT, "enums": ENUM_MEANINGS, "fields": FIELD_CONSTRAINTS,
                "workflow": WORKFLOW}

    @server.tool(description="要求 intelligence:read。先读 get_profile 与 get_opportunity_lens；按首次进入时间 [since, until) 升序列出线索，时间必须含时区；limit 1–100（默认50），cursor 用上页 next_cursor；only_unbroken 默认true，只取最新拆解不匹配当前画像、视角或原料修订的卡。返回本页profile_version；画像变化后游标失效，须重新读取画像并开始分页。随后 get_cards 读取证据。")
    @threaded
    def list_opportunity_candidates(since: str, ctx: Context, until: str | None = None, limit: int = 50,
                                    cursor: str | None = None, only_unbroken: bool = True) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        try:
            return linked(read_candidates(base_dir, since, until, limit, cursor, only_unbroken))
        except ValueError as exc:
            return _error(exc)

    @server.tool(description="要求 evaluation:write。先读 get_opportunity_lens、用 get_cards 读卡内证据，再提交 evaluator（trim后1–200字符）和 items（1–20条，每条card_id、必填正整数profile_version及视角字段；须先get_profile，缺画像profile_required、非法版本invalid_profile_version、旧版本profile_version_mismatch）；逐条 accepted/rejected；非线索卡拒绝。同一卡版本与视角版本覆盖，created_at保留首次写入时间，最终发布走durable queue。")
    @threaded
    def submit_opportunity_breakdowns(evaluator: str, items: list[dict[str, Any]], ctx: Context) -> dict[str, Any]:
        auth = require_scope(ctx, EVALUATION_WRITE)
        try:
            result = save_breakdowns(base_dir, evaluator, items, token_id=auth.token_id)
        except ValueError as exc:
            return _error(exc)
        for item in result["results"]:
            if item.get("card_id"):
                item["deep_link"] = card_link(item["card_id"])
        return result

    @server.tool(description="要求 intelligence:read。拆解后查阅，每卡只取最新拆解，按created_at倒序；since为含时区ISO时间（含边界），next_step为verify/observe/skip，min_replicability为0–100整数；limit 1–100（默认20），cursor续页。需要完整证据用get_card；返回真实核实次数与最新核实结论，接触由用户完成。")
    @threaded
    def list_opportunities(ctx: Context, since: str | None = None, next_step: str | None = None,
                           min_replicability: int | None = None, limit: int = 20,
                           cursor: str | None = None) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        try:
            return linked(read_opportunities(base_dir, since, next_step, min_replicability, limit, cursor))
        except ValueError as exc:
            return _error(exc)

    @server.tool(description="要求 verification:write（仅交互式 agent）。用户在对话中说要跟进 / 不跟进时调用；核实结果用 submit_verification。state 为 following、dismissed 或 reset；reason 可选，trim 后最多 200 字符；同状态幂等。必须先有商机拆解，返回事实推导的阶段。")
    @threaded
    def set_opportunity_follow_up(card_id: str, state: str, ctx: Context, reason: str | None = None) -> dict[str, Any]:
        auth = require_scope(ctx, VERIFICATION_WRITE)
        try:
            return save_follow_up(base_dir, card_id, state, reason, actor=f"mcp:{auth.token_id}")
        except ValueError as exc:
            return {"error": str(exc), "message": "跟进状态写入失败，请检查卡片拆解、状态或理由长度"}
