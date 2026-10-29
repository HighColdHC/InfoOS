"""Internal Bearer API for versioned interest profiles."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.responses import JSONResponse

from backend.api.routes.plugin.dependencies import error_response, set_audit_meta
from backend.application.usecases.interest_profiles import push_profile
from backend.domain.intelligence.profile import ProfileValidationError
from backend.infrastructure.security.plugin_auth import INTELLIGENCE_READ, PROFILE_WRITE, PluginAuthError, authenticate_bearer
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository


router = APIRouter(prefix="/api/intelligence/profile", tags=["intelligence-profile"])


def _auth(request: Request, authorization: str | None, scope: str) -> Any:
    try:
        auth = authenticate_bearer(request.app.state.base_dir, authorization, required_scope=scope)
    except PluginAuthError as exc:
        request.state.audit_result_code = exc.code
        request.state.audit_result_summary = str(exc)
        return error_response(request, exc.code, str(exc), status_code=403 if exc.code == "insufficient_scope" else 401)
    request.state.plugin_auth = auth
    set_audit_meta(request, required_scope=scope)
    return auth


@router.put("")
def put_profile(request: Request, body: Any = Body(default=None), authorization: str | None = Header(default=None)) -> Any:
    auth = _auth(request, authorization, PROFILE_WRITE)
    if isinstance(auth, JSONResponse):
        return auth
    if not isinstance(body, dict) or not isinstance(body.get("markdown"), str):
        return error_response(request, "invalid_profile", "markdown must be a string", status_code=422)
    try:
        return push_profile(request.app.state.base_dir, body["markdown"], source=f"mcp:{auth.token_id}")
    except ProfileValidationError as exc:
        request.state.audit_result_code = "invalid_profile"
        request.state.audit_result_summary = str(exc)
        return error_response(request, "invalid_profile", str(exc), status_code=422)


@router.get("")
def get_profile(
    request: Request, version: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> Any:
    auth = _auth(request, authorization, INTELLIGENCE_READ)
    if isinstance(auth, JSONResponse):
        return auth
    if version is not None and (not version.isdecimal() or int(version) < 1):
        return error_response(request, "invalid_profile_version", "version must be a positive integer", status_code=422)
    profile = InterestProfileRepository(request.app.state.base_dir).get(int(version) if version else None)
    if profile is None:
        return error_response(request, "profile_not_found", "Interest profile was not found", status_code=404)
    return {**profile, "section_counts": {name: len(items) for name, items in profile["sections"].items()}}
