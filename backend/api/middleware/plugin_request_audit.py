"""Metadata-only audit middleware for the authenticated plugin interface."""
from __future__ import annotations

from pathlib import Path
from time import perf_counter
import re
from typing import Any
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.concurrency import run_in_threadpool

from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository
from backend.infrastructure.storage.api_tokens import ApiTokenRepository
from backend.infrastructure.storage.base import utc_now


_PLUGIN_ROOT = "/api/plugin/v1"
_CAPTURE_ROOT = "/api/capture/v1"
_MCP_ROOT = "/mcp"
_PROFILE_PATH = "/api/intelligence/profile"
_REQUEST_ID_PATTERN = re.compile(r"^req_[A-Za-z0-9_-]{1,96}$")


class PluginRequestAuditMiddleware(BaseHTTPMiddleware):
    """Record bounded request facts without parsing credentials or bodies."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not _is_audited_path(request.url.path):
            return await call_next(request)

        base_dir = Path(request.app.state.base_dir)
        request.state.request_id = await run_in_threadpool(_request_id, request, base_dir)
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            await run_in_threadpool(
                _record_request,
                base_dir,
                request,
                status_code=500,
                duration_ms=_elapsed_ms(started),
                response_bytes=None,
            )
            raise

        response.headers["X-Request-ID"] = str(request.state.request_id)
        await run_in_threadpool(
            _record_request,
            base_dir,
            request,
            status_code=int(response.status_code),
            duration_ms=_elapsed_ms(started),
            response_bytes=_response_bytes(response),
        )
        return response


def _record_request(
    base_dir: Path,
    request: Request,
    *,
    status_code: int,
    duration_ms: int,
    response_bytes: int | None,
) -> None:
    context = getattr(request.state, "plugin_auth", None)
    safe_meta = dict(getattr(request.state, "audit_meta", {}))
    if response_bytes is not None:
        safe_meta.setdefault("response_bytes", response_bytes)
    result_code, result_summary = _result(
        status_code,
        safe_meta,
        explicit_code=getattr(request.state, "audit_result_code", None),
        explicit_summary=getattr(request.state, "audit_result_summary", None),
    )
    created_at = utc_now()
    logs = ApiRequestLogRepository(base_dir)
    tokens = ApiTokenRepository(base_dir)
    with logs.connect() as conn:
        ApiRequestLogRepository.append_to_connection(
            conn,
            request_id=str(request.state.request_id),
            token_id=context.token_id if context else None,
            token_prefix=context.token_prefix if context else None,
            client_label=context.name if context else None,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            duration_ms=duration_ms,
            response_bytes=response_bytes,
            result_code=result_code,
            result_summary=result_summary,
            response_meta=safe_meta,
            created_at=created_at,
        )
        if context is not None:
            tokens.update_last_used(
                context.token_id,
                used_at=created_at,
                connection=conn,
            )


def _request_id(request: Request, base_dir: Path) -> str:
    incoming = request.headers.get("X-Request-ID", "").strip()
    logs = ApiRequestLogRepository(base_dir)
    if _REQUEST_ID_PATTERN.fullmatch(incoming) and logs.get_detail(incoming) is None:
        return incoming
    while True:
        generated = f"req_{uuid4().hex}"
        if logs.get_detail(generated) is None:
            return generated


def _result(
    status_code: int,
    meta: dict[str, Any],
    *,
    explicit_code: Any = None,
    explicit_summary: Any = None,
) -> tuple[str, str]:
    if explicit_code and explicit_summary:
        return str(explicit_code)[:80], str(explicit_summary)[:240]
    if status_code == 206:
        return "asset_range_returned", "Returned asset byte range"
    if status_code == 401:
        return "unauthorized", "Authentication required"
    if status_code == 403:
        return "forbidden", "Request scope is insufficient"
    if status_code == 404:
        return "not_found", "Requested resource was not found"
    if status_code == 416:
        return "range_not_satisfiable", "Requested byte range was not satisfiable"
    if status_code >= 500:
        return "internal_error", "Plugin request failed"
    if 200 <= status_code < 300:
        if "card_count" in meta:
            count = max(0, int(meta["card_count"]))
            return "cards_returned", f"Returned {count} cards"
        if "card_id" in meta:
            return "card_returned", "Returned card metadata"
        if "asset_id" in meta:
            return "asset_returned", "Returned asset"
        return "ok", "Request completed"
    return "request_failed", "Plugin request failed"


def _response_bytes(response: Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((perf_counter() - started) * 1000)))


def _is_audited_path(path: str) -> bool:
    return (
        path == _PLUGIN_ROOT
        or path.startswith(f"{_PLUGIN_ROOT}/")
        or path == _CAPTURE_ROOT
        or path.startswith(f"{_CAPTURE_ROOT}/")
        or path == _MCP_ROOT
        or path.startswith(f"{_MCP_ROOT}/")
        or path == _PROFILE_PATH
    )
