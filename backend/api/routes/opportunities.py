"""Reader-authorized opportunity projections and the sole reader mutation."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from backend.application.usecases.opportunity_follow_up import set_opportunity_follow_up
from backend.domain.intelligence.opportunity import CandidateKind
from backend.read_models.opportunity_board import all_opportunities, board, detail
from backend.read_models.opportunity_stats import stats

class OpportunityRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def checked(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return _error("invalid_request", "请求字段或查询参数无效")
        return checked


router = APIRouter(route_class=OpportunityRoute)


def _error(code: str, message: str, *, status_code: int = 422) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status_code)


class FollowUpRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    state: str
    reason: str | None = None


@router.api_route("/api/opportunities/board", methods=["GET", "HEAD"])
def read_board(request: Request, platform: str | None = None, monetization_model: str | None = None,
               query: str | None = None, include_observe: bool = False,
               inbox_limit: int = 20, inbox_offset: int = 0, candidate_kind: CandidateKind | None = None):
    if not 1 <= inbox_limit <= 100 or inbox_offset < 0:
        return _error("invalid_pagination", "inbox_limit 须为 1–100，inbox_offset 不得小于 0")
    return board(Path(request.app.state.base_dir), platform, monetization_model, query,
                 include_observe, inbox_limit, inbox_offset, candidate_kind)


@router.api_route("/api/opportunities/stats", methods=["GET", "HEAD"])
def read_stats(request: Request, window: Literal["7d", "30d", "all"] = "all"):
    return stats(Path(request.app.state.base_dir), window)


@router.get("/api/opportunities/all")
def read_all(request: Request, platform: str | None = None, monetization_model: str | None = None,
             query: str | None = None, next_step: Literal["verify", "observe", "skip"] | None = None,
             stage: Literal["inbox", "plan", "contact", "action", "archive", "unstaged"] | None = None,
             candidate_kind: CandidateKind | None = None, limit: int = 20, offset: int = 0):
    if not 1 <= limit <= 100 or offset < 0:
        return _error("invalid_pagination", "limit 须为 1–100，offset 不得小于 0")
    return all_opportunities(Path(request.app.state.base_dir), platform=platform,
        monetization_model=monetization_model, query=query, next_step=next_step, stage=stage,
        candidate_kind=candidate_kind, limit=limit, offset=offset)


@router.api_route("/api/opportunities/{card_id}", methods=["GET", "HEAD"])
def read_detail(request: Request, card_id: str):
    result = detail(Path(request.app.state.base_dir), card_id)
    if result is None:
        return _error("opportunity_breakdown_required", "该卡片不存在商机拆解", status_code=404)
    return result


@router.put("/api/opportunities/{card_id}/follow-up")
def write_follow_up(request: Request, card_id: str, body: FollowUpRequest):
    try:
        return set_opportunity_follow_up(Path(request.app.state.base_dir), card_id,
                                         body.state, body.reason, actor="web")
    except ValueError as exc:
        code = str(exc)
        messages = {"card_not_found": "卡片不存在", "opportunity_breakdown_required": "请先让 agent 拆解该商机",
            "invalid_follow_up_state": "只能写入 following、dismissed 或 reset；接触结果请交给 agent",
            "follow_up_reason_too_long": "跟进理由最多 200 字符"}
        return _error(code, messages[code], status_code=404 if code == "card_not_found" else 422)
