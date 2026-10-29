"""Setup wizard HTTP routes.

The wizard talks to the backend through these endpoints; the backend
mutates ``~/.infoos/setup-state.json`` and emits the
``setup-complete.flag`` sentinel on Card 4 success.

Endpoints:
- GET  /api/setup/status                  # full state dict
- POST /api/setup/reset                   # reset setup to fresh
- POST /api/setup/dismiss                 # mark setup skipped
- POST /api/setup/launch                  # Card 1
- POST /api/setup/verify-extension        # Card 2
- POST /api/setup/verify/{platform}       # Card 3 verify
- POST /api/setup/skip/{platform}         # Card 3 mark skipped
- POST /api/setup/first-fetch             # Card 4
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from backend.application.setup import actions
from backend.infrastructure.runtime.environment import browser_console_url, setup_complete_flag_path, setup_state_path
from backend.application.setup.launch import launch_infoos_chrome
from backend.application.workflows.setup_first_fetch import submit_first_fetch
from backend.infrastructure.browser.console_access import COOKIE_NAME, cookie_max_age_seconds, issue_grant

router = APIRouter(prefix="/api/setup", tags=["setup"])

SETUP_STATE_PATH = setup_state_path()
SETUP_COMPLETE_FLAG = setup_complete_flag_path()

@router.get("/status")
def get_status() -> dict[str, Any]:
    return actions.read_status(SETUP_STATE_PATH)


@router.post("/reset")
def post_reset() -> dict[str, Any]:
    return actions.reset_setup(SETUP_STATE_PATH, SETUP_COMPLETE_FLAG)


@router.post("/dismiss")
def post_dismiss() -> dict[str, Any]:
    return actions.dismiss_setup(SETUP_STATE_PATH)


@router.post("/launch")
def post_launch(response: Response) -> dict[str, Any]:
    """Open the dedicated InfoOS Chrome window.

    This is **not** a flow step. It is a fire-and-forget helper that
    the wizard calls when the user clicks "Launch dedicated Chrome".
    It does not advance ``current_step``, does not mark a
    ``completed_step``, and does not touch any opencli bridge state.
    """
    result = launch_infoos_chrome()
    if not result["ok"]:
        # Always 200 so the frontend can surface the error inline instead
        # of throwing an unhandled promise rejection.
        return {"ok": False, "error": result["error"]}
    response.set_cookie(
        COOKIE_NAME,
        issue_grant(),
        max_age=cookie_max_age_seconds(),
        httponly=True,
        secure=True,
        samesite="strict",
        path="/browser-console/",
    )
    return {"ok": True, "console_url": browser_console_url()}


@router.post("/verify-extension")
def post_verify_extension() -> dict[str, Any]:
    """Discover the OpenCLI Browser Bridge context and bind it.

    The actual flow lives in the setup application actions. This explicit
    user action may start the OpenCLI daemon and safely relaunch only the
    dedicated InfoOS Chrome profile so the launcher can prove and name its
    Browser Bridge context. Cookies and profile data are preserved.
    """
    return actions.verify_extension_binding(SETUP_STATE_PATH)


@router.post("/verify/{platform}")
def post_verify_platform(platform: str) -> dict[str, Any]:
    """Synchronously prove login survives a graceful dedicated-Chrome restart."""
    return actions.verify_platform_login(SETUP_STATE_PATH, platform)


@router.post("/skip/{platform}")
def post_skip_platform(platform: str) -> dict[str, Any]:
    return actions.skip_platform(SETUP_STATE_PATH, platform)


@router.post("/first-fetch")
def post_first_fetch(request: Request) -> JSONResponse:
    base_dir = Path(request.app.state.base_dir)
    result = submit_first_fetch(base_dir)
    current_step = actions.read_status(SETUP_STATE_PATH)["current_step"]
    if not result["ok"]:
        return JSONResponse(
            {
                "ok": False,
                "current_step": current_step,
                "fetched_count": 0,
                "errors": result["errors"],
            },
            status_code=400,
        )
    batch = result["batch"]
    return JSONResponse(
        {
            "ok": True,
            "queued": True,
            "current_step": current_step,
            "fetched_count": 0,
            "batch": {
                **batch,
                "progress_url": f"/api/task-batches/{batch['batch_id']}",
                "status_url": f"/api/task-batches/{batch['batch_id']}",
            },
        },
        status_code=202,
    )
