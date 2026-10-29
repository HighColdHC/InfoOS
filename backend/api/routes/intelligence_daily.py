"""Local management controls and authenticated brief reading projection."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request

from backend.application.usecases.daily_brief import build_daily_brief
from backend.infrastructure.runtime.environment import public_base_url
from backend.infrastructure.storage.daily_briefs import DailyBriefRepository

router = APIRouter(tags=["intelligence-daily"])


@router.api_route("/api/intelligence/daily-brief/{brief_date}", methods=["GET", "HEAD"])
def get_daily_brief(request: Request, brief_date: str) -> dict:
    try:
        date.fromisoformat(brief_date)
    except ValueError as exc:
        raise HTTPException(422, "invalid brief date") from exc
    brief = DailyBriefRepository(request.app.state.base_dir).get(brief_date)
    if brief is None:
        raise HTTPException(404, "brief_not_found")
    # Resolve deployment URLs at read time, without changing the frozen payload.
    origin = public_base_url() or ""

    def link(path: str) -> str:
        return f"{origin}{path}" if path.startswith("/") else path

    brief = {**brief, "deep_link": link(f"/r/brief/{brief_date}"),
             "relative_url": not bool(origin),
             "items": [{**item, "deep_link": link(item["deep_link"]),
                        "relative_url": not bool(origin)} for item in brief["items"]]}
    weekly = brief.get("weekly_paused")
    if weekly and weekly.get("top"):
        brief["weekly_paused"] = {**weekly, "top": {**weekly["top"],
            "deep_link": link(weekly["top"]["deep_link"]), "relative_url": not bool(origin)}}
    return brief


@router.post("/api/intelligence/daily-brief/{brief_date}/rebuild")
def rebuild_daily_brief(request: Request, brief_date: str, force: bool = False) -> dict:
    if not force:
        raise HTTPException(422, "force=true required")
    try:
        return build_daily_brief(request.app.state.base_dir, brief_date, force=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
