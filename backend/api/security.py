"""Shared browser-mutation request guards."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware


_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_EXEMPT_EXACT_PATHS = frozenset({"/healthz"})
_BEARER_TOKEN_PREFIXES = (
    "/api/capture/v1",
    "/api/plugin/v1",
    "/api/intelligence/profile",
    "/mcp",
)


def is_same_origin_exempt_path(path: str) -> bool:
    """Return whether *path* belongs to a non-session HTTP interface."""
    if path in _EXEMPT_EXACT_PATHS:
        return True
    return any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in _BEARER_TOKEN_PREFIXES
    )


def require_same_origin_mutation(request: Request) -> None:
    """Reject browser-carried mutations without exact same-origin evidence."""
    fetch_site = str(request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        raise HTTPException(status_code=403, detail="same-origin request required")

    candidate = str(
        request.headers.get("origin") or request.headers.get("referer") or ""
    ).strip()
    if not candidate:
        raise HTTPException(status_code=403, detail="same-origin request required")
    parsed = urlsplit(candidate)
    forwarded_host = str(
        request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    ).strip().lower()
    forwarded_proto = str(
        request.headers.get("x-forwarded-proto") or request.url.scheme or ""
    ).strip().lower()
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not forwarded_host
        or parsed.netloc.lower() != forwarded_host
        or parsed.scheme.lower() != forwarded_proto
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise HTTPException(status_code=403, detail="same-origin request required")


class SameOriginManagementMiddleware(BaseHTTPMiddleware):
    """Require same-origin evidence for session-style management mutations."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if (
            request.method.upper() in _MUTATION_METHODS
            and not is_same_origin_exempt_path(request.url.path)
        ):
            try:
                require_same_origin_mutation(request)
            except HTTPException as exc:
                detail = exc.detail
                if request.url.path.startswith("/api/lan-account"):
                    from backend.application.usecases.lan_account import audit
                    audit("change", None, request.headers.get("x-real-ip", "unknown"), "cross_origin")
                    detail = "请从同一站点提交账号修改"
                return JSONResponse(
                    {"detail": detail},
                    status_code=exc.status_code,
                    headers=exc.headers,
                )
        return await call_next(request)


__all__ = [
    "SameOriginManagementMiddleware",
    "is_same_origin_exempt_path",
    "require_same_origin_mutation",
]
