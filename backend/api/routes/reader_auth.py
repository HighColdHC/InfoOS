"""Data-free nginx auth_request endpoint for the read-only reader surface."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Request, Response

from backend.infrastructure.runtime.webauth import gateway_authorized

router = APIRouter(tags=["reader-auth"])
_READER_PATH = re.compile(
    r"(?:/favicon\.ico|/r/.*|/assets/.+|/api/card-preview|/api/card-preview/[^/]+|"
    r"/api/opportunities/(?:board|stats|[^/]+)|/api/card-preview/assets/[^/]+|/api/intelligence/(?:daily-brief/[^/]+|knowledge(?:/[^/]+)?))"
)


def is_reader_request(uri: str, method: str) -> bool:
    if method not in {"GET", "HEAD", "PUT"} or not uri.startswith("/") or uri.startswith("//"):
        return False
    parsed = urlsplit(uri)
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or parsed.fragment or "\\" in path:
        return False
    if any(part in {".", ".."} for part in path.split("/")):
        return False
    if method == "PUT":
        return re.fullmatch(r"/api/opportunities/[^/]+/follow-up", path) is not None
    return _READER_PATH.fullmatch(path) is not None


@router.get("/api/internal/reader-authorize", include_in_schema=False)
def authorize_reader(request: Request) -> Response:
    if request.headers.get("x-infoos-internal-auth") != "1":
        return Response(status_code=401)
    uri = request.headers.get("x-original-uri", "")
    method = request.headers.get("x-original-method", "")
    if not is_reader_request(uri, method):
        return Response(status_code=401)
    allowed = gateway_authorized(
        request.headers.get("x-infoos-original-cookie", ""),
        edge_secret=request.headers.get("x-infoos-edge-auth", ""),
        original_uri=uri,
    )
    return Response(status_code=204 if allowed else 401)
