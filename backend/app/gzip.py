"""Compress selected HTTP representations without buffering MCP or media."""

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

COMPRESSIBLE_TYPES = frozenset({
    "application/json", "application/javascript", "text/javascript", "text/css",
    "text/html", "text/plain", "image/svg+xml",
})


class SelectiveGZipMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _excluded(scope):
            await self.app(scope, receive, send)
            return

        async def selected_app(scope: Scope, receive: Receive, gzip_send: Send) -> None:
            target = send

            async def route(message: Message) -> None:
                nonlocal target
                if message["type"] == "http.response.start":
                    content_type = Headers(raw=message["headers"]).get("content-type", "")
                    target = gzip_send if content_type.split(";", 1)[0].strip().lower() in COMPRESSIBLE_TYPES else send
                await target(message)

            await self.app(scope, receive, route)

        await GZipMiddleware(selected_app, minimum_size=1024, compresslevel=5)(scope, receive, send)


def _excluded(scope: Scope) -> bool:
    path = scope["path"]
    return path == "/mcp" or path.startswith("/mcp/") or "range" in Headers(scope=scope)
