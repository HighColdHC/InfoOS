"""Shared request-scoped HTTP transport pinned to a validated IP address."""
from __future__ import annotations

import base64
import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from requests.utils import get_environ_proxies, select_proxy
from urllib3 import connection as urllib3_connection
from urllib3 import exceptions as urllib3_exceptions
from urllib3.util import connection as urllib3_util_connection

from backend.infrastructure.embedded_proxy_dns import (
    is_embedded_proxy,
    resolve_embedded_proxy_hostname,
)

_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "instance-data", "instance-data.ec2.internal"}
)
_PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_PROXY_FAKE_IPV6_COMPANION_NETWORK = ipaddress.ip_network("fdfe:dcba:9876::/64")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class PublicHttpHop:
    url: str
    status_code: int
    location: str | None


@dataclass(frozen=True)
class OpenedPublicUrl:
    response: Any
    final_url: str
    hops: tuple[PublicHttpHop, ...]


@dataclass(frozen=True)
class ResolvedPublicUrl:
    final_url: str
    hops: tuple[PublicHttpHop, ...]


class _PinnedHTTPConnection(urllib3_connection.HTTPConnection):
    def __init__(self, *args: Any, pinned_ip: str, **kwargs: Any) -> None:
        self.pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        return urllib3_util_connection.create_connection(
            (self.pinned_ip, self.port),
            self.timeout,
            source_address=self.source_address,
            socket_options=self.socket_options,
        )


class _PinnedHTTPSConnection(urllib3_connection.HTTPSConnection):
    def __init__(self, *args: Any, pinned_ip: str, **kwargs: Any) -> None:
        self.pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        return urllib3_util_connection.create_connection(
            (self.pinned_ip, self.port),
            self.timeout,
            source_address=self.source_address,
            socket_options=self.socket_options,
        )


class PinnedResponse:
    def __init__(self, connection: Any, response: Any) -> None:
        self._connection = connection
        self._response = response
        self.status_code = int(response.status)
        self.headers = response.headers

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise urllib3_exceptions.HTTPError(f"HTTP status {self.status_code}")

    def iter_content(self, chunk_size: int):
        yield from self._response.stream(chunk_size, decode_content=True)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


class _HTTPClientResponse:
    def __init__(self, connection: http.client.HTTPConnection, response: http.client.HTTPResponse) -> None:
        self._connection = connection
        self._response = response
        self.status_code = int(response.status)
        self.headers = response.headers

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise urllib3_exceptions.HTTPError(f"HTTP status {self.status_code}")

    def iter_content(self, chunk_size: int):
        while True:
            chunk = self._response.read(chunk_size)
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


def open_pinned_url(
    url: str,
    *,
    pinned_ip: str,
    proxy_url: str | None = None,
    headers: dict[str, str],
    timeout: int | tuple[float, float],
    method: str = "GET",
    body: bytes | None = None,
) -> PinnedResponse | _HTTPClientResponse:
    """Open URL at pinned_ip while authenticating and addressing the original host."""
    if proxy_url is not None:
        return _open_proxy_pinned_url(
            url,
            pinned_ip=pinned_ip,
            proxy_url=proxy_url,
            headers=headers,
            timeout=timeout,
            method=method,
            body=body,
        )
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    connect_timeout, read_timeout = _split_timeout(timeout)
    if parsed.scheme.lower() == "https":
        connection = _PinnedHTTPSConnection(
            host=hostname,
            port=port,
            timeout=connect_timeout,
            pinned_ip=pinned_ip,
            cert_reqs=ssl.CERT_REQUIRED,
            assert_hostname=hostname,
            server_hostname=hostname,
        )
    else:
        connection = _PinnedHTTPConnection(
            host=hostname,
            port=port,
            timeout=connect_timeout,
            pinned_ip=pinned_ip,
        )
    try:
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(read_timeout)
        request_headers = dict(headers)
        request_headers["Host"] = _host_header(hostname, port, parsed.scheme.lower())
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        if body is None:
            connection.request(method, target, headers=request_headers)
        else:
            connection.request(method, target, body=body, headers=request_headers)
        return PinnedResponse(connection, connection.getresponse())
    except Exception:
        connection.close()
        raise


def open_public_url(
    url: str,
    *,
    allowed_hosts: frozenset[str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | tuple[float, float] = (3.05, 20),
    max_redirects: int = 5,
    method: str = "GET",
    body: bytes | None = None,
    resolver: Any = socket.getaddrinfo,
    opener: Any = None,
) -> OpenedPublicUrl | None:
    """Open one public URL while validating and pinning every redirect hop."""
    current_url = url
    hops: list[PublicHttpHop] = []
    request_opener = opener or open_pinned_url
    for redirect_count in range(max_redirects + 1):
        proxy_url = select_supported_proxy(current_url)
        pinned_ip = validate_and_pin_public_url(
            current_url,
            allowed_hosts=allowed_hosts,
            proxy_url=proxy_url,
            resolver=resolver,
        )
        if pinned_ip is None:
            return None
        request_kwargs = {
            "pinned_ip": pinned_ip,
            "proxy_url": proxy_url,
            "timeout": timeout,
            "headers": dict(headers or {}),
        }
        if method != "GET" or body is not None:
            request_kwargs.update({"method": method, "body": body})
        response = request_opener(current_url, **request_kwargs)
        status_code = response.status_code
        location = response.headers.get("location") or None
        hops.append(PublicHttpHop(current_url, status_code, location))
        if status_code not in _REDIRECT_STATUSES:
            try:
                response.raise_for_status()
            except Exception:
                response.close()
                raise
            return OpenedPublicUrl(response, current_url, tuple(hops))
        response.close()
        if redirect_count == max_redirects or location is None:
            return None
        current_url = urljoin(current_url, location)
        if status_code == 303:
            method = "GET"
            body = None
    return None


def read_public_url(
    url: str,
    *,
    allowed_hosts: frozenset[str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | tuple[float, float] = (3.05, 20),
    max_bytes: int = 5 * 1024 * 1024,
    max_redirects: int = 5,
    resolver: Any = socket.getaddrinfo,
    opener: Any = None,
) -> tuple[bytes, OpenedPublicUrl] | None:
    """Read a bounded public response without weakening the pinned transport."""
    opened: OpenedPublicUrl | None = None
    try:
        opened = open_public_url(
            url,
            allowed_hosts=allowed_hosts,
            headers=headers,
            timeout=timeout,
            max_redirects=max_redirects,
            resolver=resolver,
            opener=opener,
        )
        if opened is None:
            return None
        content_length = str(opened.response.headers.get("content-length") or "")
        if content_length.isdigit() and int(content_length) > max_bytes:
            return None
        content = bytearray()
        for chunk in opened.response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > max_bytes:
                return None
        if not content:
            return None
        return bytes(content), opened
    finally:
        if opened is not None:
            opened.response.close()


def resolve_public_url(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    terminal_url: Any,
    headers: dict[str, str] | None = None,
    timeout: int | tuple[float, float] = (3.05, 20),
    max_redirects: int = 5,
    resolver: Any = socket.getaddrinfo,
    opener: Any = None,
) -> ResolvedPublicUrl | None:
    """Resolve safe redirects, allowing a validated target to end the chain early."""
    current_url = url
    hops: list[PublicHttpHop] = []
    request_opener = opener or open_pinned_url
    for redirect_count in range(max_redirects + 1):
        proxy_url = select_supported_proxy(current_url)
        pinned_ip = validate_and_pin_public_url(
            current_url,
            allowed_hosts=allowed_hosts,
            proxy_url=proxy_url,
            resolver=resolver,
        )
        if pinned_ip is None:
            return None
        response = request_opener(
            current_url,
            pinned_ip=pinned_ip,
            proxy_url=proxy_url,
            timeout=timeout,
            headers=dict(headers or {}),
        )
        status_code = response.status_code
        location = response.headers.get("location") or None
        hops.append(PublicHttpHop(current_url, status_code, location))
        if status_code not in _REDIRECT_STATUSES:
            try:
                response.raise_for_status()
            finally:
                response.close()
            return ResolvedPublicUrl(current_url, tuple(hops))
        response.close()
        if redirect_count == max_redirects or location is None:
            return None
        next_url = urljoin(current_url, location)
        next_proxy = select_supported_proxy(next_url)
        if validate_and_pin_public_url(
            next_url,
            allowed_hosts=allowed_hosts,
            proxy_url=next_proxy,
            resolver=resolver,
        ) is None:
            return None
        if terminal_url(next_url):
            return ResolvedPublicUrl(next_url, tuple(hops))
        current_url = next_url
    return None


def select_supported_proxy(url: str) -> str | None:
    """Return an explicit HTTP(S) proxy selected by the standard proxy environment."""
    proxy_url = select_proxy(url, get_environ_proxies(url))
    if not proxy_url:
        return None
    try:
        parsed = urlsplit(proxy_url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None
    return proxy_url


def validate_and_pin_public_url(
    url: str,
    *,
    allowed_hosts: frozenset[str] | None,
    proxy_url: str | None = None,
    resolver: Any = socket.getaddrinfo,
    allow_nonstandard_port: bool = False,
) -> str | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (
            not allow_nonstandard_port
            and port not in ({None, 443} if scheme == "https" else {None, 80})
        )
        or hostname in _BLOCKED_HOSTNAMES
        or hostname.endswith(".localhost")
    ):
        return None
    if allowed_hosts is not None and (scheme != "https" or hostname not in allowed_hosts):
        return None
    return _resolve_public_ip(
        hostname,
        port or (443 if scheme == "https" else 80),
        resolver,
        allow_proxy_fake_ip=proxy_url is not None,
        proxy_url=proxy_url,
    )


def _resolve_public_ip(
    hostname: str,
    port: int,
    resolver: Any,
    *,
    allow_proxy_fake_ip: bool,
    proxy_url: str | None = None,
) -> str | None:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return str(literal) if literal.is_global else None
    try:
        records = resolver(hostname, port, type=socket.SOCK_STREAM)
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    except (OSError, UnicodeError, IndexError, ValueError):
        return None
    if not addresses:
        return None
    if all(address.is_global for address in addresses):
        return str(sorted(addresses, key=lambda address: (address.version, int(address)))[0])
    fake_ipv4 = {
        address
        for address in addresses
        if address.version == 4 and address in _PROXY_FAKE_IP_NETWORK
    }
    companion_ipv6 = {
        address
        for address in addresses
        if address.version == 6 and address in _PROXY_FAKE_IPV6_COMPANION_NETWORK
    }
    if fake_ipv4 and addresses == fake_ipv4 | companion_ipv6:
        if is_embedded_proxy(proxy_url):
            return resolve_embedded_proxy_hostname(hostname)
        if allow_proxy_fake_ip:
            return str(sorted(fake_ipv4, key=lambda address: int(address))[0])
    return None


def _open_proxy_pinned_url(
    url: str,
    *,
    pinned_ip: str,
    proxy_url: str,
    headers: dict[str, str],
    timeout: int | tuple[float, float],
    method: str,
    body: bytes | None,
) -> _HTTPClientResponse:
    parsed = urlsplit(url)
    proxy = urlsplit(proxy_url)
    hostname = parsed.hostname or ""
    origin_port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    proxy_hostname = proxy.hostname or ""
    proxy_port = proxy.port or (443 if proxy.scheme.lower() == "https" else 80)
    connect_timeout, read_timeout = _split_timeout(timeout)
    raw_socket: socket.socket | None = None
    proxy_connection: http.client.HTTPConnection | None = None
    connection: http.client.HTTPConnection | None = None
    try:
        proxy_authorization = _proxy_authorization_header(proxy)
        if parsed.scheme.lower() == "https":
            proxy_connection_class = (
                http.client.HTTPSConnection
                if proxy.scheme.lower() == "https"
                else http.client.HTTPConnection
            )
            proxy_connection = proxy_connection_class(
                proxy_hostname,
                proxy_port,
                timeout=connect_timeout,
            )
            tunnel_headers = {"Host": _authority(pinned_ip, origin_port)}
            if proxy_authorization:
                tunnel_headers["Proxy-Authorization"] = proxy_authorization
            proxy_connection.set_tunnel(pinned_ip, origin_port, headers=tunnel_headers)
            proxy_connection.connect()
            raw_socket = proxy_connection.sock
            if raw_socket is None:
                raise OSError("proxy tunnel did not provide a socket")
            proxy_connection.sock = None
            raw_socket.settimeout(read_timeout)
            raw_socket = ssl.create_default_context().wrap_socket(
                raw_socket,
                server_hostname=hostname,
            )
            connection = http.client.HTTPSConnection(hostname, origin_port, timeout=read_timeout)
            connection.sock = raw_socket
            target = _origin_form_target(parsed)
        else:
            raw_socket = socket.create_connection(
                (proxy_hostname, proxy_port),
                timeout=connect_timeout,
            )
            if proxy.scheme.lower() == "https":
                raw_socket = ssl.create_default_context().wrap_socket(
                    raw_socket,
                    server_hostname=proxy_hostname,
                )
            connection = http.client.HTTPConnection(hostname, origin_port, timeout=read_timeout)
            connection.sock = raw_socket
            target = f"http://{_authority(pinned_ip, origin_port)}{_origin_form_target(parsed)}"
        raw_socket.settimeout(read_timeout)
        request_headers = dict(headers)
        request_headers["Host"] = _host_header(hostname, origin_port, parsed.scheme.lower())
        if parsed.scheme.lower() == "http" and proxy_authorization:
            request_headers["Proxy-Authorization"] = proxy_authorization
        if body is None:
            connection.request(method, target, headers=request_headers)
        else:
            connection.request(method, target, body=body, headers=request_headers)
        return _HTTPClientResponse(connection, connection.getresponse())
    except Exception:
        if connection is not None:
            connection.close()
        elif raw_socket is not None:
            raw_socket.close()
        if proxy_connection is not None:
            proxy_connection.close()
        raise


def _proxy_authorization_header(proxy: Any) -> str | None:
    if proxy.username is None:
        return None
    username = unquote(proxy.username)
    password = unquote(proxy.password or "")
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {encoded}"


def _origin_form_target(parsed: Any) -> str:
    target = parsed.path or "/"
    return f"{target}?{parsed.query}" if parsed.query else target


def _authority(hostname: str, port: int) -> str:
    return f"[{hostname}]:{port}" if ":" in hostname else f"{hostname}:{port}"


def _host_header(hostname: str, port: int, scheme: str) -> str:
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    return host if port == default_port else f"{host}:{port}"


def _split_timeout(timeout: int | tuple[float, float]) -> tuple[float, float]:
    if isinstance(timeout, tuple):
        return float(timeout[0]), float(timeout[1])
    return float(timeout), float(timeout)
