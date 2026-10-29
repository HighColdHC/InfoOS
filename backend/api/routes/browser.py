"""/api/browser/* routes — session lifecycle, login actions and console access.

Endpoints:
- GET  /api/browser/session       — current dedicated browser session state
- POST /api/browser/session       — launch the dedicated browser session
- POST /api/browser/login-check   — passive platform login summary
- POST /api/browser/login-launch  — open a platform login URL
- GET  /api/browser/console-authorize — internal console authorization
- WS   /api/browser/console-websocket — authorized console proxy
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response, WebSocket, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.application.setup import browser_session
from backend.api.browser_console import proxy_browser_console

from backend.infrastructure.runtime.environment import setup_state_path
from backend.infrastructure.browser.console_access import (
    COOKIE_NAME,
    authorize_grant,
    cookie_max_age_seconds,
    issue_grant,
)
from backend.infrastructure.runtime.webauth import gateway_authorized


router = APIRouter(prefix="/api/browser", tags=["browser"])

SETUP_STATE_PATH = setup_state_path()


class BrowserSessionAction(BaseModel):
    action: str | None = None


class BrowserPlatformAction(BaseModel):
    platform: str | None = None


@router.get("/session")
def get_browser_session() -> dict:
    """Return the current dedicated browser session state, if any."""
    return browser_session.read_browser_session()


@router.post("/session", status_code=status.HTTP_202_ACCEPTED)
def post_browser_session(body: BrowserSessionAction, response: Response):
    """Launch a new dedicated browser session. Returns 202 + task_id."""
    if body.action != "launch":
        return _error("unsupported_browser_action", "Unsupported browser action", 422)
    result = browser_session.launch_browser_session()
    if not result["ok"]:
        return _error("browser_launch_failed", result["error"], 422)
    _set_console_cookie(response)
    return result


@router.post("/login-check")
def post_browser_login_check(body: BrowserPlatformAction):
    """Refresh a single platform's login state from local Chrome cookies."""
    if not body.platform:
        return _error("platform_required", "platform is required", 422)
    try:
        return browser_session.check_platform_login(body.platform, state_path=SETUP_STATE_PATH)
    except ValueError:
        return _error("unsupported_login_platform", "Unsupported login platform", 422)


@router.post("/login-launch")
def post_browser_login_launch(body: BrowserPlatformAction, response: Response):
    """Open the dedicated InfoOS Chrome window at a platform login URL."""
    if not body.platform:
        return _error("platform_required", "platform is required", 422)
    try:
        result = browser_session.launch_platform_login(body.platform)
        if result["ok"]:
            _set_console_cookie(response)
        return result
    except ValueError:
        return _error("unsupported_login_platform", "Unsupported login platform", 422)


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status_code)


@router.get("/console-authorize", include_in_schema=False)
def get_browser_console_authorization(request: Request) -> Response:
    if request.headers.get("x-infoos-internal-auth") != "1":
        return Response(status_code=404)
    allowed = gateway_authorized(
        request.headers.get("x-infoos-original-cookie", ""),
        edge_secret=request.headers.get("x-infoos-edge-auth", ""),
        edge_authenticated=request.headers.get("x-infoos-edge-authenticated") == "1",
    ) and authorize_grant(
        request.headers.get("x-infoos-console-grant", ""),
        origin=request.headers.get("x-infoos-original-origin", ""),
        referer=request.headers.get("x-infoos-original-referer", ""),
        forwarded_host=request.headers.get("x-forwarded-host", ""),
        forwarded_proto=request.headers.get("x-forwarded-proto", ""),
    )
    return Response(status_code=204 if allowed else 401)


@router.websocket("/console-websocket")
async def browser_console_websocket(websocket: WebSocket) -> None:
    await proxy_browser_console(websocket)


def _set_console_cookie(response: Response) -> None:
    response.set_cookie(
        COOKIE_NAME,
        issue_grant(),
        max_age=cookie_max_age_seconds(),
        httponly=True,
        secure=True,
        samesite="strict",
        path="/browser-console/",
    )
