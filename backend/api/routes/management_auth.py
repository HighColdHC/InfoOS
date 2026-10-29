"""Private nginx authorization for the appliance management surface."""
from fastapi import APIRouter, Request, Response

from backend.infrastructure.runtime.webauth import edge_secret_authorized, gateway_authorized

router = APIRouter(tags=["management-auth"])


@router.get("/api/internal/management-authorize", include_in_schema=False)
def authorize_management(request: Request) -> Response:
    if request.headers.get("x-infoos-internal-auth") != "1":
        return Response(status_code=404)
    edge_allowed = edge_secret_authorized(request.headers.get("x-infoos-edge-auth", ""))
    allowed = gateway_authorized(
        request.headers.get("x-infoos-original-cookie", ""),
        edge_authenticated=edge_allowed,
        original_uri=request.headers.get("x-original-uri", "/"),
    )
    return Response(
        status_code=204 if allowed else 401,
        headers={"X-InfoOS-Edge-Authenticated": "1" if edge_allowed else ""},
    )
