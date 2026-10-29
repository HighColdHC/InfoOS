"""Authenticated workbench routes for manual platform auth recovery."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from backend.application.workflows.auth_recovery import (
    AuthRecoveryNotActionable,
    AuthRecoveryNotFound,
    AuthRecoveryRuntimeUnavailable,
    AuthRecoverySuperseded,
    AuthRecoveryUnsupported,
    get_auth_incident,
    open_auth_console,
    verify_auth_recovery,
)
from backend.infrastructure.browser.console_access import (
    COOKIE_NAME,
    cookie_max_age_seconds,
    issue_grant,
)
from backend.api.security import require_same_origin_mutation


router = APIRouter(prefix="/api/auth-recoveries", tags=["auth-recovery"])


@router.get("/{incident_id}")
def get_recovery(request: Request, incident_id: str):
    try:
        incident = get_auth_incident(request.app.state.base_dir, incident_id)
    except AuthRecoveryNotFound:
        return _error("auth_recovery_not_found", "Auth recovery incident not found", 404)
    return {"ok": True, "incident": incident}


@router.post(
    "/{incident_id}/open-console",
    status_code=202,
    dependencies=[Depends(require_same_origin_mutation)],
)
def open_console(request: Request, response: Response, incident_id: str):
    try:
        console_url, incident = open_auth_console(
            request.app.state.base_dir,
            incident_id,
        )
    except AuthRecoveryNotFound:
        return _error("auth_recovery_not_found", "Auth recovery incident not found", 404)
    except AuthRecoveryNotActionable:
        return _error("recovery_not_actionable", "Auth recovery is not actionable", 409)
    except AuthRecoveryUnsupported:
        return _error(
            "unsupported_login_platform",
            "Unsupported login platform",
            422,
        )
    except AuthRecoveryRuntimeUnavailable:
        return _error(
            "browser_runtime_unavailable",
            "Browser runtime is unavailable",
            503,
        )
    _set_console_cookie(response)
    return {"ok": True, "console_url": console_url, "incident": incident}


@router.post(
    "/{incident_id}/verify",
    dependencies=[Depends(require_same_origin_mutation)],
)
def verify_recovery(request: Request, incident_id: str):
    try:
        return verify_auth_recovery(request.app.state.base_dir, incident_id)
    except AuthRecoveryNotFound:
        return _error("auth_recovery_not_found", "Auth recovery incident not found", 404)
    except AuthRecoveryNotActionable:
        return _error("recovery_not_actionable", "Auth recovery is not actionable", 409)
    except AuthRecoverySuperseded:
        return _error("recovery_superseded", "Auth recovery was superseded", 409)
    except AuthRecoveryRuntimeUnavailable:
        return _error(
            "recovery_verification_unavailable",
            "Auth recovery verification is temporarily unavailable",
            503,
        )


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


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status_code)


@router.post('/{incident_id}/qr-round', status_code=202, dependencies=[Depends(require_same_origin_mutation)])
def qr_round(request: Request, incident_id: str):
    from backend.application.workflows.auth_qr import start_qr_round, QrRoundUnavailable
    try:
        return {'ok': True, 'round': start_qr_round(request.app.state.base_dir, incident_id, manual=True)}
    except QrRoundUnavailable as exc:
        return _error(str(exc), '二维码轮次暂时无法启动', 404 if str(exc) == 'auth_recovery_not_found' else 409)
