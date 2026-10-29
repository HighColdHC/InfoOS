from __future__ import annotations

import json
import ssl
import socket

from backend.application.enrichment.media import artifacts
from backend.infrastructure import public_http


MINIMAL_JPEG = b"\xff\xd8\xff\xe0image"


class _Response:
    def __init__(self, *, status_code=200, location=None, chunks=(MINIMAL_JPEG,), headers=None):
        self.status_code = status_code
        self.headers = {"content-type": "image/jpeg", **(headers or {})}
        if location is not None:
            self.headers["location"] = location
        self._chunks = chunks
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        self.closed = True


class _ControllerResponse:
    def __init__(self, payload=None, *, status=200, body=None, headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = body if body is not None else json.dumps(payload).encode("utf-8")
        self.closed = False

    def getheader(self, name):
        return self.headers.get(name)

    def read(self, size=-1):
        chunk = self._body[:size]
        self._body = self._body[len(chunk) :]
        return chunk

    def close(self):
        self.closed = True


def _address_record(address: str, port: int = 443):
    return [(2, 1, 6, "", (address, port))]


def _ipv6_address_record(address: str, port: int = 443):
    return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, port, 0, 0))]


def test_direct_media_download_rejects_non_public_literal_without_request(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(artifacts, "open_pinned_url", lambda *args, **kwargs: calls.append(args))

    result = artifacts.download_direct_media_url(
        "http://169.254.169.254/latest/meta-data/iam.jpg",
        assets_dir=tmp_path,
        index=1,
    )

    assert result is None
    assert calls == []


def test_direct_media_download_rejects_hostname_with_any_private_dns_answer(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _address_record("93.184.216.34") + _address_record("10.0.0.7"),
    )
    monkeypatch.setattr(artifacts, "open_pinned_url", lambda *args, **kwargs: calls.append(args))

    result = artifacts.download_direct_media_url(
        "https://cdn.example.test/image.jpg",
        assets_dir=tmp_path,
        index=1,
    )

    assert result is None
    assert calls == []


def test_direct_media_download_routes_proxy_fake_ip_through_explicit_http_proxy(
    monkeypatch,
    tmp_path,
):
    requested = []
    response = _Response()
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INFOOS_EMBEDDED_PROXY_ENABLED", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _address_record("198.18.0.5"),
    )

    def open_url(url, **kwargs):
        requested.append((url, kwargs))
        return response

    monkeypatch.setattr(artifacts, "open_pinned_url", open_url)

    result = artifacts.download_direct_media_url(
        "https://cdn.example.test/image.jpg",
        assets_dir=tmp_path,
        index=1,
    )

    assert result is not None
    assert requested == [
        (
            "https://cdn.example.test/image.jpg",
            {
                "pinned_ip": "198.18.0.5",
                "proxy_url": "http://127.0.0.1:7897",
                "timeout": (10, 20),
                "headers": {"User-Agent": "InfoOS/0.1 (+https://local.infoos)"},
            },
        )
    ]
    assert response.closed is True


def test_public_reader_uses_proxy_fake_ipv4_when_clash_also_returns_companion_ipv6(
    monkeypatch,
):
    requested = []
    response = _Response(chunks=(b"<html><body>example</body></html>",))
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (
            _address_record("198.18.0.5")
            + _ipv6_address_record("fdfe:dcba:9876::75")
        ),
    )

    def open_url(url, **kwargs):
        requested.append((url, kwargs))
        return response

    monkeypatch.setattr(artifacts, "open_pinned_url", open_url)

    result = artifacts.read_public_url("https://example.test/article")

    assert result is not None
    assert requested[0][1]["pinned_ip"] == "198.18.0.5"
    assert requested[0][1]["proxy_url"] == "http://127.0.0.1:7897"
    assert response.closed is True


def test_embedded_proxy_fake_ip_uses_mihomo_global_controller_answers(monkeypatch):
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INFOOS_EMBEDDED_PROXY_ENABLED", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:17897")
    monkeypatch.setenv("INFOOS_EMBEDDED_PROXY_CONTROL_PORT", "19090")
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _address_record("198.18.0.5"),
    )

    controller_requests = []

    class ControllerConnection:
        responses = [
            _ControllerResponse(
                {
                    "Answer": [
                        {"type": 5, "data": "源.example.test"},
                        {"type": 1, "data": "93.184.216.34"},
                    ]
                }
            ),
            _ControllerResponse({"Answer": []}),
        ]

        def __init__(self, host, port, timeout):
            controller_requests.append(("connect", host, port, timeout))

        def request(self, method, path, headers):
            controller_requests.append((method, path, headers))

        def getresponse(self):
            return self.responses.pop(0)

        def close(self):
            controller_requests.append(("close",))

    monkeypatch.setattr(public_http.http.client, "HTTPConnection", ControllerConnection)
    opened = []

    def open_url(url, **kwargs):
        opened.append((url, kwargs))
        return _Response()

    result = public_http.open_public_url(
        "https://源.example.test/image.jpg",
        resolver=lambda *_args, **_kwargs: _address_record("198.18.0.5"),
        opener=open_url,
    )

    assert result is not None
    assert opened[0][1]["pinned_ip"] == "93.184.216.34"
    assert opened[0][1]["proxy_url"] == "http://127.0.0.1:17897"
    assert controller_requests[0] == ("connect", "127.0.0.1", 19090, 2.0)
    assert [entry[1] for entry in controller_requests if entry[0] == "GET"] == [
        "/dns/query?name=%E6%BA%90.example.test&type=A",
        "/dns/query?name=%E6%BA%90.example.test&type=AAAA",
    ]


def test_embedded_proxy_controller_rejects_mixed_private_answer(monkeypatch):
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INFOOS_EMBEDDED_PROXY_ENABLED", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:17897")
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _address_record("198.18.0.5"),
    )
    controller_requests = []

    class ControllerConnection:
        responses = [
            _ControllerResponse(
                {
                    "Answer": [
                        {"type": 1, "data": "93.184.216.34"},
                        {"type": 1, "data": "10.0.0.7"},
                    ]
                }
            )
        ]

        def __init__(self, host, port, timeout):
            controller_requests.append((host, port, timeout))

        def request(self, *_args, **_kwargs):
            return None

        def getresponse(self):
            return self.responses.pop(0)

        def close(self):
            return None

    monkeypatch.setattr(public_http.http.client, "HTTPConnection", ControllerConnection)
    opener_calls = []

    result = public_http.open_public_url(
        "https://origin.example.test/image.jpg",
        resolver=lambda *_args, **_kwargs: _address_record("198.18.0.5"),
        opener=lambda *args, **kwargs: opener_calls.append((args, kwargs)),
    )

    assert result is None
    assert controller_requests == [("127.0.0.1", 19090, 2.0)]
    assert opener_calls == []


def test_embedded_proxy_controller_failure_fails_closed(monkeypatch):
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INFOOS_EMBEDDED_PROXY_ENABLED", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:17897")
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _address_record("198.18.0.5"),
    )

    class ControllerConnection:
        def __init__(self, *_args, **_kwargs):
            raise OSError("controller unavailable")

    monkeypatch.setattr(public_http.http.client, "HTTPConnection", ControllerConnection)
    opener_calls = []

    result = public_http.open_public_url(
        "https://origin.example.test/image.jpg",
        resolver=lambda *_args, **_kwargs: _address_record("198.18.0.5"),
        opener=lambda *args, **kwargs: opener_calls.append((args, kwargs)),
    )

    assert result is None
    assert opener_calls == []


def test_public_reader_rejects_private_dns_answer_mixed_with_proxy_fake_addresses(
    monkeypatch,
):
    calls = []
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (
            _address_record("198.18.0.5")
            + _ipv6_address_record("fdfe:dcba:9876::75")
            + _address_record("10.0.0.7")
        ),
    )
    monkeypatch.setattr(
        artifacts,
        "open_pinned_url",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = artifacts.read_public_url("https://example.test/article")

    assert result is None
    assert calls == []


def test_direct_media_download_rejects_proxy_fake_ip_without_proxy(monkeypatch, tmp_path):
    calls = []
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _address_record("198.18.0.5"),
    )
    monkeypatch.setattr(artifacts, "open_pinned_url", lambda *args, **kwargs: calls.append(args))

    result = artifacts.download_direct_media_url(
        "https://cdn.example.test/image.jpg",
        assets_dir=tmp_path,
        index=1,
    )

    assert result is None
    assert calls == []


def test_direct_media_download_revalidates_public_redirect_target(monkeypatch, tmp_path):
    requested = []
    first = _Response(status_code=302, location="https://cdn.example.test/final.jpg", chunks=())
    final = _Response()

    def resolve(hostname, port, **_kwargs):
        assert hostname in {"origin.example.test", "cdn.example.test"}
        return _address_record("93.184.216.34", port)

    def open_url(url, **kwargs):
        requested.append((url, kwargs))
        return first if len(requested) == 1 else final

    monkeypatch.setattr(artifacts.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(artifacts, "open_pinned_url", open_url)

    result = artifacts.download_direct_media_url(
        "https://origin.example.test/image.jpg",
        assets_dir=tmp_path,
        index=1,
    )

    assert result is not None
    assert [entry[0] for entry in requested] == [
        "https://origin.example.test/image.jpg",
        "https://cdn.example.test/final.jpg",
    ]
    assert [entry[1]["pinned_ip"] for entry in requested] == [
        "93.184.216.34",
        "93.184.216.34",
    ]
    assert first.closed is True
    assert final.closed is True


def test_direct_media_download_rejects_redirect_to_private_dns_target(monkeypatch, tmp_path):
    requested = []
    first = _Response(status_code=302, location="https://internal.example.test/secret.jpg", chunks=())

    def resolve(hostname, port, **_kwargs):
        address = "93.184.216.34" if hostname == "origin.example.test" else "192.168.1.10"
        return _address_record(address, port)

    monkeypatch.setattr(artifacts.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        artifacts,
        "open_pinned_url",
        lambda url, **kwargs: requested.append(url) or first,
    )

    result = artifacts.download_direct_media_url(
        "https://origin.example.test/image.jpg",
        assets_dir=tmp_path,
        index=1,
    )

    assert result is None
    assert requested == ["https://origin.example.test/image.jpg"]
    assert first.closed is True


def test_direct_media_download_rejects_credentials_and_nonstandard_ports(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(artifacts, "open_pinned_url", lambda *args, **kwargs: calls.append(args))

    assert artifacts.download_direct_media_url(
        "https://user@example.com/image.jpg", assets_dir=tmp_path, index=1
    ) is None
    assert artifacts.download_direct_media_url(
        "https://example.com:8443/image.jpg", assets_dir=tmp_path, index=2
    ) is None
    assert calls == []


def test_public_url_reader_enforces_actual_byte_limit_without_content_length(monkeypatch):
    response = _Response(chunks=(b"a" * 6, b"b" * 6), headers={})
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _address_record("93.184.216.34"),
    )
    monkeypatch.setattr(artifacts, "open_pinned_url", lambda *_args, **_kwargs: response)

    result = artifacts.read_public_url("https://example.test/article", max_bytes=10)

    assert result is None
    assert response.closed is True


def test_public_url_reader_pins_each_redirect_hop_against_sequential_dns_rebinding(monkeypatch):
    dns_answers = {
        "origin.example.test": iter(("93.184.216.34", "10.0.0.10")),
        "cdn.example.test": iter(("93.184.216.35", "192.168.1.20")),
    }
    connected_addresses = []
    responses = iter(
        (
            _Response(status_code=302, location="https://cdn.example.test/final.jpg", chunks=()),
            _Response(),
        )
    )

    def resolve(hostname, port, **_kwargs):
        return _address_record(next(dns_answers[hostname]), port)

    def open_url(_url, *, pinned_ip, **_kwargs):
        connected_addresses.append(pinned_ip)
        return next(responses)

    monkeypatch.setattr(artifacts.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(artifacts, "open_pinned_url", open_url)

    artifacts.read_public_url("https://origin.example.test/article")

    assert connected_addresses == ["93.184.216.34", "93.184.216.35"]


def test_public_redirect_resolver_can_stop_at_validated_canonical_target():
    response = _Response(
        status_code=302,
        location="https://www.iesdouyin.com/share/video/9000000000000000004/?token=private",
        chunks=(),
    )

    resolved = public_http.resolve_public_url(
        "https://v.douyin.com/example/",
        allowed_hosts=frozenset({"v.douyin.com", "www.iesdouyin.com"}),
        terminal_url=lambda value: "/share/video/" in value,
        resolver=lambda _host, port, **_kwargs: _address_record("93.184.216.34", port),
        opener=lambda *_args, **_kwargs: response,
    )

    assert resolved is not None
    assert resolved.final_url.startswith(
        "https://www.iesdouyin.com/share/video/9000000000000000004/"
    )
    assert len(resolved.hops) == 1
    assert response.closed is True


def test_public_redirect_resolver_rejects_private_terminal_target():
    response = _Response(
        status_code=302,
        location="http://169.254.169.254/latest/meta-data/",
        chunks=(),
    )

    resolved = public_http.resolve_public_url(
        "https://v.douyin.com/example/",
        allowed_hosts=frozenset({"v.douyin.com", "169.254.169.254"}),
        terminal_url=lambda _value: True,
        resolver=lambda _host, port, **_kwargs: _address_record("93.184.216.34", port),
        opener=lambda *_args, **_kwargs: response,
    )

    assert resolved is None
    assert response.closed is True


def test_pinned_https_connection_uses_ip_for_tcp_and_hostname_for_tls(monkeypatch):
    sentinel_socket = object()
    connected = []
    monkeypatch.setattr(
        public_http.urllib3_util_connection,
        "create_connection",
        lambda address, *_args, **_kwargs: connected.append(address) or sentinel_socket,
    )
    connection = public_http._PinnedHTTPSConnection(
        host="origin.example.test",
        port=443,
        timeout=3,
        pinned_ip="93.184.216.34",
        cert_reqs=ssl.CERT_REQUIRED,
        assert_hostname="origin.example.test",
        server_hostname="origin.example.test",
    )

    assert connection._new_conn() is sentinel_socket
    assert connected == [("93.184.216.34", 443)]
    assert connection.host == "origin.example.test"
    assert connection.server_hostname == "origin.example.test"
    assert connection.assert_hostname == "origin.example.test"
    assert connection.cert_reqs == ssl.CERT_REQUIRED


def test_proxy_https_connection_tunnels_to_fake_ip_and_keeps_origin_tls_hostname(monkeypatch):
    events = []

    class Socket:
        def settimeout(self, value):
            events.append(("socket_timeout", value))

    class ProxyConnection:
        def __init__(self, host, port, timeout):
            events.append(("proxy", host, port, timeout))
            self.sock = None

        def set_tunnel(self, host, port, headers):
            events.append(("tunnel", host, port, headers))

        def connect(self):
            self.sock = Socket()

        def close(self):
            events.append(("proxy_close",))

    class OriginResponse:
        status = 200
        headers = {"content-type": "image/jpeg"}

        def read(self, _size):
            return b""

        def close(self):
            events.append(("response_close",))

    class OriginConnection:
        def __init__(self, host, port, timeout):
            events.append(("origin", host, port, timeout))
            self.sock = None

        def request(self, method, target, headers):
            events.append(("request", method, target, headers))

        def getresponse(self):
            return OriginResponse()

        def close(self):
            events.append(("origin_close",))

    class TLSContext:
        def wrap_socket(self, sock, server_hostname):
            events.append(("tls", server_hostname))
            return sock

    monkeypatch.setattr(public_http.http.client, "HTTPConnection", ProxyConnection)
    monkeypatch.setattr(public_http.http.client, "HTTPSConnection", OriginConnection)
    monkeypatch.setattr(public_http.ssl, "create_default_context", lambda: TLSContext())

    response = public_http.open_pinned_url(
        "https://origin.example.test/image.jpg?size=large",
        pinned_ip="198.18.0.5",
        proxy_url="http://127.0.0.1:7897",
        headers={"User-Agent": "InfoOS test"},
        timeout=(3, 7),
    )
    response.close()

    assert ("proxy", "127.0.0.1", 7897, 3.0) in events
    assert (
        "tunnel",
        "198.18.0.5",
        443,
        {"Host": "198.18.0.5:443"},
    ) in events
    assert ("tls", "origin.example.test") in events
    assert events.index(("socket_timeout", 7.0)) < events.index(
        ("tls", "origin.example.test")
    )
    assert ("origin", "origin.example.test", 443, 7.0) in events
    assert (
        "request",
        "GET",
        "/image.jpg?size=large",
        {"User-Agent": "InfoOS test", "Host": "origin.example.test"},
    ) in events
