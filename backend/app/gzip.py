"""Compress selected HTTP representations without buffering MCP or media."""

from starlette.datastructures import Headers, MutableHeaders
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
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        headers = Headers(scope=scope)
        if path == "/mcp" or path.startswith("/mcp/") or "range" in headers:
            await self.app(scope, receive, send)
            return

        async def filtered_app(scope: Scope, receive: Receive, gzip_send: Send) -> None:
            selected = False

            async def select_response(message: Message) -> None:
                nonlocal selected
                if message["type"] == "http.response.start":
                    response_headers = Headers(raw=message["headers"])
                    content_type = response_headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    selected = message["status"] != 206 and content_type in COMPRESSIBLE_TYPES
                    if selected:
                        vary = response_headers.get("vary", "")
                        if "accept-encoding" not in {part.strip().lower() for part in vary.split(",")}:
                            MutableHeaders(raw=message["headers"]).add_vary_header("Accept-Encoding")
                        content_length = response_headers.get("content-length")
                        if content_length is not None and int(content_length) < 1024:
                            selected = False
                await (gzip_send if selected else send)(message)

            await self.app(scope, receive, select_response)

        async def send_without_duplicate_vary(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(raw=message["headers"])
                vary = response_headers.get("vary", "")
                parts = [part.strip() for part in vary.split(",") if part.strip()]
                if sum(part.lower() == "accept-encoding" for part in parts) > 1:
                    seen = False
                    unique = []
                    for part in parts:
                        if part.lower() == "accept-encoding":
                            if seen:
                                continue
                            seen = True
                        unique.append(part)
                    response_headers["vary"] = ", ".join(unique)
            await send(message)

        await GZipMiddleware(filtered_app, minimum_size=1024, compresslevel=5)(
            scope, receive, send_without_duplicate_vary
        )
