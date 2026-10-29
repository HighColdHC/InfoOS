"""Bearer gate for every MCP transport request."""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.concurrency import run_in_threadpool

from backend.api.routes.plugin.dependencies import error_response, set_audit_meta
from backend.infrastructure.security.plugin_auth import PluginAuthError, authenticate_bearer


class McpBearerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path != "/mcp" and not request.url.path.startswith("/mcp/"):
            return await call_next(request)
        try:
            auth = await run_in_threadpool(
                authenticate_bearer,
                Path(request.app.state.base_dir), request.headers.get("authorization"),
                required_scope=None,
            )
        except PluginAuthError as exc:
            request.state.audit_result_code = exc.code
            request.state.audit_result_summary = str(exc)
            return error_response(
                request, exc.code, str(exc),
                status_code=403 if exc.code == "insufficient_scope" else 401,
            )
        request.state.plugin_auth = auth
        set_audit_meta(request)
        return await call_next(request)
