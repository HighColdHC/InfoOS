"""Reader-authorized opportunity projections and the sole reader mutation."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from backend.application.usecases.set_research_status import set_research_status
from backend.read_models.opportunity_board import board
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


MESSAGES = {"invalid_research_status": "调研状态只能是 pending、researching、project、observe、drop",
    "invalid_subtype": "子类型只能是 case_business、running_ads、selling、wind_vane",
    "invalid_note": "备注最多 200 字符", "not_opportunity_card": "只有商机知识卡可以设置调研状态",
    "knowledge_not_found": "知识卡不存在"}


class ResearchStatusRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    status: str
    note: str | None = None


@router.api_route("/api/opportunities/board", methods=["GET", "HEAD"])
def read_board(request: Request, platform: str | None = None, subtype: str | None = None,
               query: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0):
    if not 1 <= limit <= 100 or offset < 0:
        return _error("invalid_pagination", "limit 须为 1–100，offset 不得小于 0")
    try:
        return board(Path(request.app.state.base_dir), platform=platform or None, subtype=subtype or None,
                     query=query, status=status or None, limit=limit, offset=offset)
    except ValueError as exc:
        code = str(exc)
        return _error(code, MESSAGES[code])


@router.api_route("/api/opportunities/stats", methods=["GET", "HEAD"])
def read_stats(request: Request, window: Literal["7d", "30d", "all"] = "all"):
    return stats(Path(request.app.state.base_dir), window)


@router.put("/api/opportunities/{knowledge_id}/research-status")
def write_research_status(request: Request, knowledge_id: str, body: ResearchStatusRequest):
    try:
        return set_research_status(Path(request.app.state.base_dir), knowledge_id, body.status, body.note, actor="web")
    except ValueError as exc:
        code = str(exc)
        return _error(code, MESSAGES[code], status_code=404 if code == "knowledge_not_found" else 422)
