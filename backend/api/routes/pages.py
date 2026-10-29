"""Frontend document routes.

FastAPI owns API endpoints and serves the React build artifact when it is
available. Reader data is exposed through ``/api/*`` routes; the document routes
serve the management and reader SPA entrypoints.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from backend.application.usecases.reader_page_views import record_reader_page_view

router = APIRouter()


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


def _frontend_dist_dir(request: Request) -> Path:
    base_dir = _base_dir(request)
    return base_dir / "frontend" / "dist"


def _frontend_index(request: Request) -> Path:
    return _frontend_dist_dir(request) / "index.html"


def _serve_frontend_index(request: Request) -> Response:
    frontend_index = _frontend_index(request)
    if frontend_index.exists():
        return FileResponse(frontend_index)
    return JSONResponse({"error": "frontend_dist_missing"}, status_code=404)


def _serve_reader(request: Request, page: str, target_id: str | None = None) -> Response:
    response = _serve_frontend_index(request)
    if request.method == "GET" and response.status_code == 200:
        record_reader_page_view(_base_dir(request), page, target_id,
                                request.query_params.get("from"), request.headers.get("user-agent"))
    return response


@router.get("/", response_class=HTMLResponse, response_model=None)
def index(request: Request) -> Response:
    return _serve_frontend_index(request)


@router.api_route("/favicon.ico", methods=["GET", "HEAD"])
def favicon() -> Response:
    return Response(status_code=204)


@router.get("/healthz")
def healthz() -> dict:
    """Lightweight web-process liveness check."""
    return {"status": "ok"}


@router.api_route("/r/card/{card_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def card_reader(request: Request, card_id: str) -> Response:
    return _serve_reader(request, "card", card_id)


@router.api_route("/r/brief/{brief_date}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def brief_reader(request: Request, brief_date: str) -> Response:
    return _serve_reader(request, "brief", brief_date)


@router.api_route("/r/browse", methods=["GET", "HEAD"], response_class=HTMLResponse)
def browse_reader(request: Request) -> Response:
    return _serve_reader(request, "browse")


@router.api_route("/r/opportunities", methods=["GET", "HEAD"], response_class=HTMLResponse)
@router.api_route("/r/opportunities/stats", methods=["GET", "HEAD"], response_class=HTMLResponse)
def opportunities_reader(request: Request) -> Response:
    return _serve_reader(request, "opportunity_stats" if request.url.path.endswith("/stats") else "opportunities")


@router.api_route("/r/knowledge", methods=["GET", "HEAD"], response_class=HTMLResponse)
def knowledge_library_reader(request: Request) -> Response:
    return _serve_reader(request, "knowledge_library")


@router.api_route("/r/knowledge/{knowledge_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def knowledge_reader(request: Request, knowledge_id: str) -> Response:
    return _serve_reader(request, "knowledge", knowledge_id)
