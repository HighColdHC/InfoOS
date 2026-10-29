"""Authenticated knowledge reading projection."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.read_models.knowledge import browse_knowledge_library, get_knowledge_card

router = APIRouter(tags=["intelligence-knowledge"])


@router.api_route("/api/intelligence/knowledge", methods=["GET", "HEAD"])
def knowledge_library(request: Request, kind: str | None = None, subtype: str | None = None,
                      research_status: str | None = None, query: str | None = None,
                      limit: int = 20, cursor: str | None = None):
    try:
        return browse_knowledge_library(Path(request.app.state.base_dir), kind, subtype, research_status,
                                        query, limit, cursor)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)


@router.api_route("/api/intelligence/knowledge/{knowledge_id}", methods=["GET", "HEAD"])
def knowledge_detail(request: Request, knowledge_id: str) -> dict:
    value = get_knowledge_card(Path(request.app.state.base_dir), knowledge_id)
    if value is None:
        raise HTTPException(404, "knowledge_not_found")
    return value
