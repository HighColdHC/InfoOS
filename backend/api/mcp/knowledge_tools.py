"""Seven scope-checked knowledge tools, sharing the existing auth scopes."""
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP

from backend.api.mcp.offload import threaded
from backend.application.usecases.set_research_status import set_research_status as save_status
from backend.application.usecases.submit_knowledge_extractions import submit_knowledge_extractions as save_extractions
from backend.domain.intelligence.knowledge import CHANNEL_TEXT, CONFUSIONS, EXTRACTION_VERSION, WORKFLOW
from backend.domain.intelligence.knowledge_kinds import enabled_schema
from backend.infrastructure.runtime.knowledge_config import read_pkm_targets
from backend.infrastructure.security.plugin_auth import EVALUATION_WRITE, INTELLIGENCE_READ, VERIFICATION_WRITE
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.read_models import knowledge


def register_knowledge_tools(server: FastMCP, base_dir: Path, require_scope: Callable) -> None:
    @server.tool(description="读取 knowledge_v1 拆解标准、字段约束、子类型、归并键和当前画像版本。" + WORKFLOW)
    @threaded
    def get_knowledge_schema(ctx: Context) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        profile = InterestProfileRepository(base_dir).get()
        return {"extraction_version": EXTRACTION_VERSION, "kinds": enabled_schema(read_pkm_targets()), "confusions": CONFUSIONS,
                "deterministic_channels": CHANNEL_TEXT, "workflow": WORKFLOW,
                "submission_fields": "new/update的类型字段放在fields对象内，共有字段在卡对象顶层。",
                "profile_version": profile["profile_version"] if profile is not None else None}

    @server.tool(description="按入库时间升序读取 active 且当前版本未拆解的候选；时间须含时区，[since,until)，limit 1–100，空cursor等同不传；画像变化后游标失效。确定性渠道不受分诊排除影响。")
    @threaded
    def list_extraction_candidates(since: str, ctx: Context, until: str | None = None,
                                    limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        return knowledge.list_extraction_candidates(base_dir, since, until, limit, cursor)

    @server.tool(description="归并前检索同类型知识卡，关键词按空格分词，每词均须出现在title或fields中；limit 1–20，默认10。由agent判断new/merge/update/conflict。")
    @threaded
    def search_knowledge_cards(kind: str, query: str, ctx: Context, limit: int = 10) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        return knowledge.search_knowledge_cards(base_dir, kind, query, limit)

    @server.tool(description="要求 evaluation:write。evaluator 必填，items 1–10条，每条card_id、profile_version、outcome；nothing须nothing_reason；extracted须cards 1–8张。new/update的类型字段放在fields对象内，共有字段在顶层；merge/conflict只需action、kind、target_knowledge_id、quotes、note，update/conflict的note必填。quotes须原样复制。逐条事务返回accepted/rejected，不覆盖拆解、不触发finalize。" + WORKFLOW)
    @threaded
    def submit_knowledge_extractions(evaluator: str, items: list[dict[str, Any]], ctx: Context) -> dict[str, Any]:
        auth = require_scope(ctx, EVALUATION_WRITE)
        return save_extractions(base_dir, evaluator, items, token_id=auth.token_id)

    @server.tool(description="按updated_at倒序列出知识卡，支持类型、更新时间下限、调研状态和归属逐字筛选；limit 1–100，cursor续页。")
    @threaded
    def list_knowledge_cards(ctx: Context, kind: str | None = None, since: str | None = None,
                             research_status: str | None = None, attribution_ref: str | None = None,
                             limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        return knowledge.list_knowledge_cards(base_dir, kind, since, research_status, attribution_ref, limit, cursor)

    @server.tool(description="读取知识卡字段、全部原卡证据和摘句、冲突标记、商机调研状态历史及同源知识卡。")
    @threaded
    def get_knowledge_card(knowledge_id: str, ctx: Context) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        value = knowledge.get_knowledge_card(base_dir, knowledge_id)
        return value if value is not None else {"error": "knowledge_not_found", "knowledge_id": knowledge_id}

    @server.tool(description="要求verification:write，只由交互式agent根据用户调研决定调用；只用于商机卡。status取pending/researching/project/observe/drop，note可选最多200字。同状态重复调用不写事件。")
    @threaded
    def set_research_status(knowledge_id: str, status: str, ctx: Context, note: str | None = None) -> dict[str, Any]:
        auth = require_scope(ctx, VERIFICATION_WRITE)
        return save_status(base_dir, knowledge_id, status, note, actor=f"mcp:{auth.token_id}")
