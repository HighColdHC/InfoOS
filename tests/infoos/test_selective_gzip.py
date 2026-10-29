"""HTTP compression contracts for API, frontend assets and excluded traffic."""

import gzip

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from backend.app.gzip import SelectiveGZipMiddleware
from backend.app.main import create_app


def _raw(client, path, encoding="gzip", **headers):
    with client.stream("GET", path, headers={"Accept-Encoding": encoding, **headers}) as response:
        return response.status_code, response.headers, b"".join(response.iter_raw())


def test_api_and_frontend_assets_compress_without_changing_content(tmp_path):
    assets = tmp_path / "frontend" / "dist" / "assets"
    assets.mkdir(parents=True)
    for name in ("app.js", "app.css"):
        (assets / name).write_text("example content " * 200, encoding="utf-8")
    app = create_app(tmp_path)

    @app.get("/api/gzip-test")
    def large_json():
        return JSONResponse({"content": "example content " * 200}, headers={"Vary": "Origin, Accept-Encoding"})

    # Add the test route before the existing catch-all MCP mount.
    app.router.routes.insert(0, app.router.routes.pop())
    client = TestClient(app)
    for path in ("/api/gzip-test", "/assets/app.js", "/assets/app.css"):
        _, plain_headers, plain = _raw(client, path, "identity")
        status, headers, compressed = _raw(client, path)
        assert status == 200
        assert headers["content-encoding"] == "gzip"
        assert gzip.decompress(compressed) == plain
        assert headers["vary"].lower().split(", ").count("accept-encoding") == 1
        if path.startswith("/assets/"):
            assert headers["cache-control"] == plain_headers["cache-control"] == "public, max-age=31536000, immutable"


def test_excluded_responses_and_small_body(tmp_path):
    assets = tmp_path / "data" / "assets" / "card_1"
    assets.mkdir(parents=True)
    content = b"image content " * 200
    (assets / "image.jpg").write_bytes(content)
    app = create_app(tmp_path)

    @app.get("/api/small-gzip-test")
    def small():
        return Response("small", media_type="text/plain")

    app.router.routes.insert(0, app.router.routes.pop())
    client = TestClient(app)
    _, headers, body = _raw(client, "/api/small-gzip-test")
    assert body == b"small" and "content-encoding" not in headers
    _, headers, body = _raw(client, "/api/assets/card_1/image.jpg")
    assert body == content and "content-encoding" not in headers
    status, headers, body = _raw(client, "/api/assets/card_1/image.jpg", Range="bytes=0-99")
    assert status == 206 and body == content[:100] and "content-encoding" not in headers

    standalone = FastAPI()
    standalone.add_middleware(SelectiveGZipMiddleware)

    @standalone.get("/mcp/test")
    def mcp():
        return Response("stream " * 500, media_type="text/plain")

    _, headers, body = _raw(TestClient(standalone), "/mcp/test")
    assert body == b"stream " * 500 and "content-encoding" not in headers
