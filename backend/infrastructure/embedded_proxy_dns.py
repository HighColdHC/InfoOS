"""Fail-closed Mihomo controller DNS fallback for embedded proxy fake IPs."""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
from typing import Any
from urllib.parse import quote

_DEFAULT_MIXED_PORT = 17897
_DEFAULT_CONTROL_PORT = 19090
_MAX_RESPONSE_BYTES = 64 * 1024
_QUERY_TIMEOUT_SECONDS = 2.0
_READ_CHUNK_SIZE = 16 * 1024


def is_embedded_proxy(proxy_url: str | None) -> bool:
    """Return whether *proxy_url* is exactly InfoOS's loopback mixed listener."""
    if os.environ.get("INFOOS_EMBEDDED_PROXY_ENABLED") != "1" or proxy_url is None:
        return False
    mixed_port = _configured_loopback_port("INFOOS_EMBEDDED_PROXY_MIXED_PORT", _DEFAULT_MIXED_PORT)
    return mixed_port is not None and proxy_url == f"http://127.0.0.1:{mixed_port}"


def resolve_embedded_proxy_hostname(hostname: str) -> str | None:
    """Resolve *hostname* through Mihomo and return one validated global IP."""
    addresses: set[Any] = set()
    for record_type in ("A", "AAAA"):
        resolved = _query_dns(hostname, record_type)
        if resolved is None:
            return None
        addresses.update(resolved)
    if not addresses:
        return None
    return str(sorted(addresses, key=lambda address: (address.version, int(address)))[0])


def _configured_loopback_port(name: str, default: int) -> int | None:
    try:
        port = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


def _query_dns(hostname: str, record_type: str) -> set[Any] | None:
    control_port = _configured_loopback_port("INFOOS_EMBEDDED_PROXY_CONTROL_PORT", _DEFAULT_CONTROL_PORT)
    if control_port is None:
        return None
    query_path = f"/dns/query?name={quote(hostname, safe='')}&type={record_type}"
    connection: http.client.HTTPConnection | None = None
    response: Any = None
    try:
        # This is deliberately a direct loopback connection.  Do not use the
        # process proxy environment for the controller request itself.
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            control_port,
            timeout=_QUERY_TIMEOUT_SECONDS,
        )
        connection.request(
            "GET",
            query_path,
            headers={"Accept": "application/json", "Connection": "close"},
        )
        response = connection.getresponse()
        if response.status != 200:
            return None
        content_length = _response_content_length(response)
        if content_length is not None and content_length > _MAX_RESPONSE_BYTES:
            return None

        body = bytearray()
        while len(body) <= _MAX_RESPONSE_BYTES:
            read_size = min(_READ_CHUNK_SIZE, _MAX_RESPONSE_BYTES + 1 - len(body))
            chunk = response.read(read_size)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > _MAX_RESPONSE_BYTES:
                return None
        return _parse_dns_payload(json.loads(bytes(body).decode("utf-8")), record_type)
    except (
        http.client.HTTPException,
        OSError,
        TimeoutError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return None
    finally:
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()


def _response_content_length(response: Any) -> int | None:
    content_length = response.getheader("Content-Length")
    if content_length is None:
        return None
    value = content_length.strip()
    if not value.isdigit():
        raise ValueError("invalid controller response content length")
    return int(value)


def _parse_dns_payload(payload: Any, record_type: str) -> set[Any] | None:
    if not isinstance(payload, dict):
        return None
    answer = payload.get("Answer")
    if answer is None:
        return set()
    if not isinstance(answer, list):
        return None

    expected_family = 4 if record_type == "A" else 6
    addresses: set[Any] = set()
    for record in answer:
        if not isinstance(record, dict):
            continue
        address_family = _address_record_family(record.get("type"))
        if address_family is None:
            continue
        if address_family != expected_family:
            return None
        data = record.get("data")
        if not isinstance(data, str) or not data.strip():
            return None
        try:
            address = ipaddress.ip_address(data.strip())
        except ValueError:
            return None
        if address.version != expected_family or not address.is_global:
            return None
        addresses.add(address)
    return addresses


def _address_record_family(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return {1: 4, 28: 6}.get(value)
    if isinstance(value, str):
        return {"A": 4, "AAAA": 6}.get(value.strip().upper())
    return None
