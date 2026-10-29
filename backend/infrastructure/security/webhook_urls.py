"""Validation for token-owned webhook destinations."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from requests.utils import get_environ_proxies, select_proxy


_PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class UnsafeWebhookUrlError(ValueError):
    pass


def validate_webhook_url(value: str, *, resolver=socket.getaddrinfo) -> str:
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeWebhookUrlError("Webhook URL is invalid") from exc
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
    ):
        raise UnsafeWebhookUrlError("Webhook URL is unsafe")

    if parsed.scheme.lower() == "http" and _is_loopback_host(host):
        return url
    if parsed.scheme.lower() != "https":
        raise UnsafeWebhookUrlError("Webhook URL must use public HTTPS or loopback HTTP")
    proxy_url = select_proxy(url, get_environ_proxies(url))
    if not _resolves_public(host, port or 443, resolver, allow_proxy_fake_ip=bool(proxy_url)):
        raise UnsafeWebhookUrlError("Webhook URL must resolve only to public addresses")
    return url


def is_loopback_webhook_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme.lower() == "http" and _is_loopback_host((parsed.hostname or "").rstrip(".").lower())


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _resolves_public(host: str, port: int, resolver, *, allow_proxy_fake_ip: bool) -> bool:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return literal.is_global
    try:
        records = resolver(host, port, type=socket.SOCK_STREAM)
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    except (OSError, UnicodeError, IndexError, ValueError):
        return False
    if not addresses:
        return False
    if all(address.is_global for address in addresses):
        return True
    return allow_proxy_fake_ip and all(address in _PROXY_FAKE_IP_NETWORK for address in addresses)
