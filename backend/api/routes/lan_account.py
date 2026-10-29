"""Credential forms guarded by appliance edge or form-login authentication."""
import ipaddress

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.application.usecases.lan_account import AccountError, account_info, audit, change_account
from backend.infrastructure.runtime.webauth import gateway_authorized

router = APIRouter(prefix="/api/lan-account", tags=["lan-account"])


def _authorized(request: Request) -> bool:
    return gateway_authorized(
        request.headers.get("cookie", ""),
        edge_authenticated=request.headers.get("x-infoos-edge-authenticated") == "1",
        original_uri="/api/lan-account",
    )


def _address(request: Request) -> str:
    # nginx replaces this header; it is never an arbitrary X-Forwarded-For chain.
    value = request.headers.get("x-real-ip", "")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return request.client.host if request.client else "unknown"


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("")
def get_account(request: Request):
    if not _authorized(request):
        return _error(401, "请先登录后再操作")
    try:
        info = account_info()
    except (OSError, ValueError):
        return _error(503, "账号服务暂不可用，请稍后重试")
    return JSONResponse(info, headers={"Cache-Control": "no-store"})


async def _change(request: Request, action: str):
    if not _authorized(request):
        audit(action, None, _address(request), "unauthenticated")
        return _error(401, "请先登录后再操作")
    # Parse without Pydantic's validation echo of sensitive input values.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            audit(action, None, _address(request), "form_too_large")
            return _error(400, "提交内容过长")
    try:
        import json
        payload = json.loads(body)
        allowed = {"current_username", "current_password", "new_password", "confirm_password", "new_username"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError
        if any(not isinstance(value, str) or len(value) > 1024 for value in payload.values()):
            raise ValueError
    except (ValueError, TypeError):
        audit(action, None, _address(request), "invalid_form")
        return _error(400, "表单格式不正确")
    try:
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(change_account, action, payload, _address(request), lambda: _authorized(request))
        response = JSONResponse(result, headers={"Cache-Control": "no-store"})
        response.delete_cookie("auth", path="/", secure=True, httponly=True, samesite="lax")
        return response
    except AccountError as exc:
        return _error(exc.status, exc.message)


@router.post("/password")
async def change_password(request: Request):
    return await _change(request, "password")


@router.post("/username")
async def change_username(request: Request):
    return await _change(request, "username")
