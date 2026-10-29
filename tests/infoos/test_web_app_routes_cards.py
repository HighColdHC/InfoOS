"""Web app route tests — frontend fallback, configuration and asset serving."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.main import create_app


def test_web_app_serves_frontend_dist_when_present(tmp_path):
    frontend_dist = tmp_path / "frontend" / "dist"
    frontend_dist.mkdir(parents=True)
    (frontend_dist / "assets").mkdir()
    (frontend_dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (frontend_dist / "index.html").write_text(
        '<div id="root">InfoOS SaaS Frontend</div><script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    app = create_app(base_dir=tmp_path)
    client = TestClient(app)

    page = client.get("/")
    detail = client.get("/cards/card_demo")
    reader = client.get("/r/card/card_demo")
    asset = client.get("/assets/app.js")

    assert page.status_code == 200
    assert detail.status_code == 404
    assert reader.status_code == 200
    assert page.headers["cache-control"] == reader.headers["cache-control"] == "no-cache"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "InfoOS SaaS Frontend" in page.text
    assert "InfoOS SaaS Frontend" in reader.text

@pytest.mark.parametrize(("filename", "media_type"), [
    ("tweet_1.jpg", "image/jpeg"),
    ("video.mp4", "video/mp4"),
    ("subtitles.vtt", "application/octet-stream"),
    ("image.svg", "application/octet-stream"),
    ("page.html", "application/octet-stream"),
])
def test_api_assets_serves_local_media_files(tmp_path, filename, media_type):
    assets_dir = tmp_path / "data" / "assets" / "card_1" / "tweets"
    assets_dir.mkdir(parents=True)
    (assets_dir / filename).write_text("fake-image", encoding="utf-8")
    app = create_app(base_dir=tmp_path)
    client = TestClient(app)

    response = client.get(f"/api/assets/card_1/tweets/{filename}")
    assert response.status_code == 200
    assert response.content == b"fake-image"
    assert response.headers["content-type"] == media_type
    assert response.headers["x-content-type-options"] == "nosniff"

    missing = client.get("/api/assets/card_1/tweets/missing.png")
    assert missing.status_code == 404

    encoded_traversal = client.get("/api/assets/%2e%2e/secret.txt")
    assert encoded_traversal.status_code == 403


def test_api_assets_uses_infoos_data_home_instead_of_same_named_program_asset(tmp_path, monkeypatch):
    program_dir = tmp_path / "program"
    runtime_data_dir = tmp_path / "runtime-data"
    relative_path = Path("card_1") / "tweets" / "tweet_1.jpg"
    stale_asset = program_dir / "data" / "assets" / relative_path
    runtime_asset = runtime_data_dir / "assets" / relative_path
    stale_asset.parent.mkdir(parents=True)
    runtime_asset.parent.mkdir(parents=True)
    stale_asset.write_bytes(b"stale-image")
    runtime_asset.write_bytes(b"runtime-image")
    monkeypatch.chdir(program_dir)
    monkeypatch.setenv("INFOOS_DATA_HOME", str(runtime_data_dir))

    client = TestClient(create_app(base_dir="."))

    response = client.get("/api/assets/card_1/tweets/tweet_1.jpg")
    assert response.status_code == 200
    assert response.content == b"runtime-image"
