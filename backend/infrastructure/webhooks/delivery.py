"""Minimal redirect-free Webhook HTTP transport."""
from __future__ import annotations

import hmac
import http.client
import json
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from urllib3.exceptions import HTTPError

from backend.domain.timestamps import utc_now_seconds_z
from backend.infrastructure.public_http import (
    open_pinned_url,
    select_supported_proxy,
    validate_and_pin_public_url,
)
from backend.infrastructure.security.webhook_urls import (
    is_loopback_webhook_url,
    validate_webhook_url,
)


class WebhookTransportError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def probe_webhook(
    url: str,
    *,
    token_digest: str,
    timeout_seconds: int = 5,
) -> int:
    """Send a body-free product event to prove the configured receiver is ready."""
    event_id = f"evt_test_{uuid4().hex}"
    timestamp = utc_now_seconds_z()
    body = json.dumps(
        {"event_id": event_id, "event": "webhook.test", "occurred_at": timestamp},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(bytes.fromhex(token_digest), body, sha256).hexdigest()
    return post_webhook(
        url,
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-InfoOS-Event-Id": event_id,
            "X-InfoOS-Timestamp": timestamp,
            "X-InfoOS-Signature": f"sha256={signature}",
        },
        timeout_seconds=timeout_seconds,
    )


def post_webhook(
    url: str,
    *,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: int = 20,
) -> int:
    validated = validate_webhook_url(url)
    if is_loopback_webhook_url(validated):
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.post(
                validated,
                data=body,
                headers=headers,
                timeout=timeout_seconds,
                allow_redirects=False,
            )
            return int(response.status_code)
        except requests.RequestException as exc:
            raise WebhookTransportError(str(exc)) from exc
        finally:
            session.close()

    proxy_url = select_supported_proxy(validated)
    host = str(urlsplit(validated).hostname or "").rstrip(".").lower()
    pinned_ip = validate_and_pin_public_url(
        validated,
        allowed_hosts=frozenset({host}),
        proxy_url=proxy_url,
        allow_nonstandard_port=True,
    )
    if pinned_ip is None:
        raise WebhookTransportError("webhook destination is no longer public")
    response = None
    try:
        response = open_pinned_url(
            validated,
            pinned_ip=pinned_ip,
            proxy_url=proxy_url,
            headers=headers,
            timeout=timeout_seconds,
            method="POST",
            body=body,
        )
        return int(response.status_code)
    except (HTTPError, OSError, ValueError, http.client.HTTPException) as exc:
        raise WebhookTransportError(str(exc)) from exc
    finally:
        if response is not None:
            response.close()
