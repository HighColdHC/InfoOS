"""Interactive-agent writeback of user-confirmed opportunity verifications."""
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP
from pydantic import StrictInt

from backend.api.mcp.offload import threaded
from backend.application.usecases.submit_verification import submit_verification as save_verification
from backend.domain.intelligence.verification import VerificationError
from backend.infrastructure.security.plugin_auth import INTELLIGENCE_READ, VERIFICATION_WRITE
from backend.read_models.verifications import list_verifications as read_verifications


SUBMIT_DESCRIPTION = (
    "要求 verification:write（仅交互式 agent）。流程：用户口述或贴图 → agent 按字段整理 → 缺必填项先追问 → "
    "把整理结果给用户确认 → 用户同意后才调用。不得写入对方联系方式与聊天全文；"
    "截图请由 agent 读取后整理为文字字段，不上传原图。任何已存在卡均可追加，不覆盖原记录。"
    "client_verification_id必填，1–128字符；一次用户确认生成一次，重试复用，新核实或更正使用新ID。"
    "同Token同ID同内容返回原结果且replayed=true，首次false；不同内容verification_conflict。"
    "可选breakdown_id和expected_breakdown_hash须同时提供；取自get_card.opportunity的breakdown_id和breakdown_content_hash，"
    "把拆解与核实一起交用户确认。服务器冻结当时快照；拆解已变化返回breakdown_changed，重新读取并确认。"
    "不关联时标记未关联AI拆解。card_id必填；recorder为agent自报，1–200字符；contact_channel为wechat/phone/platform_dm/in_person/other；"
    "offer 1–500字符；credibility为credible/doubtful/scam_suspected/unclear；"
    "conclusion为keep_observing/drop/to_action（转入PKM行动）。"
    "可选字段或null：contacted_on为YYYY-MM-DD，price≤300、delivery/funnel≤500、doubts≤800、note≤1500字符，"
    "replicability为0–100整数，supersedes为同卡既有verification_id。返回后可用list_verifications/get_card核对；发布走durable queue。"
)


def register_verification_tools(server: FastMCP, base_dir: Path, require_scope: Callable, card_link: Callable) -> None:
    @server.tool(description=SUBMIT_DESCRIPTION)
    @threaded
    def submit_verification(card_id: str, recorder: str, contact_channel: str, offer: str,
                            credibility: str, conclusion: str, client_verification_id: str, ctx: Context,
                            contacted_on: str | None = None,
                            price: str | None = None, delivery: str | None = None, funnel: str | None = None,
                            doubts: str | None = None, replicability: StrictInt | None = None,
                            note: str | None = None, supersedes: str | None = None,
                            breakdown_id: str | None = None, expected_breakdown_hash: str | None = None) -> dict[str, Any]:
        auth = require_scope(ctx, VERIFICATION_WRITE)
        try:
            result = save_verification(base_dir, {
                "card_id": card_id, "recorder": recorder, "contact_channel": contact_channel, "offer": offer,
                "credibility": credibility, "conclusion": conclusion, "contacted_on": contacted_on,
                "price": price, "delivery": delivery, "funnel": funnel, "doubts": doubts,
                "replicability": replicability, "note": note, "supersedes": supersedes,
                "client_verification_id": client_verification_id, "breakdown_id": breakdown_id,
                "expected_breakdown_hash": expected_breakdown_hash,
            }, token_id=auth.token_id)
        except VerificationError as exc:
            return exc.as_dict()
        return {**result, "deep_link": card_link(result["card_id"])}

    @server.tool(description="要求 intelligence:read。写入后或日后对照时读取人工核实记录，按记录创建时间倒序；可选card_id、since（含时区ISO时间，含边界）、conclusion（keep_observing/drop/to_action）；limit 1–100（默认20），cursor用上页next_cursor。返回卡片标题、平台及deep_link；被更正记录保留，superseded_by指向最新直接更正记录。")
    @threaded
    def list_verifications(ctx: Context, card_id: str | None = None, since: str | None = None,
                           conclusion: str | None = None, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        try:
            result = read_verifications(base_dir, card_id, since, conclusion, limit, cursor)
        except ValueError as exc:
            return {"error": str(exc), "message": "核实记录查询参数无效，请检查筛选条件、时间、条数及游标"}
        result["items"] = [{**item, "deep_link": card_link(item["card_id"])} for item in result["items"]]
        return result
