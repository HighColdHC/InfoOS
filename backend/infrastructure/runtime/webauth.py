"""Authorize appliance access through the edge or private form-login service."""
from __future__ import annotations

import os
import secrets

import httpx


def edge_secret_authorized(supplied: str) -> bool:
    configured = os.environ.get("INFOOS_EDGE_READER_SECRET", "")
    return len(configured) >= 32 and secrets.compare_digest(
        configured.encode("utf-8"), supplied.encode("utf-8")
    )


def gateway_authorized(
    cookie_header: str,
    *,
    edge_secret: str = "",
    edge_authenticated: bool = False,
    original_uri: str = "/browser-console/",
) -> bool:
    """Accept edge auth or a form session.

    ``edge_authenticated`` is the verdict of nginx's private auth subrequest;
    nginx must overwrite the corresponding header on every application proxy.
    Only auth subrequests receive the original ``edge_secret``.
    """
    return edge_authenticated or edge_secret_authorized(edge_secret) or gateway_session_authorized(
        cookie_header, original_uri=original_uri
    )


def gateway_session_authorized(cookie_header: str, *, original_uri: str = "/browser-console/") -> bool:
    """Validate the outer GUI session through the base image's UDS service."""
    if not cookie_header:
        return False
    socket_path = os.environ.get("INFOOS_WEBAUTH_SOCKET", "/tmp/webauth.sock")
    try:
        transport = httpx.HTTPTransport(uds=socket_path)
        with httpx.Client(transport=transport, timeout=2.0) as client:
            response = client.get(
                "http://localhost/auth",
                headers={"cookie": cookie_header, "x-original-uri": original_uri},
            )
    except (httpx.HTTPError, OSError):
        return False
    return 200 <= response.status_code < 300
