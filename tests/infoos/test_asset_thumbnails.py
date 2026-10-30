"""Thumbnail rendering, generation, maintenance and HTTP read contracts."""
from pathlib import Path
import json
import shutil
import subprocess
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api.main import api_router
from backend.application.enrichment.media import thumbnails as generation
from backend.infrastructure.media import thumbnails as rendering
from backend.infrastructure.storage.card_assets import CardAssetRepository
from backend.infrastructure.storage.card_asset_thumbnails import CardAssetThumbnailRepository
from backend.infrastructure.storage.cards import CardRepository
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.paths import resolve_data_dir
from backend.workers.asset_thumbnails import main
from tests.infoos.test_card_preview_api import _store_card


def store_asset(base_dir, asset_id="asset_test", *, content_hash="hash", filename="image.png"):
    _store_card(base_dir, card_id="card-thumb", platform="rss", title="Thumbnail", author="Author")
    payload = {
        "asset_id": asset_id, "card_id": "card-thumb", "asset_kind": "image",
        "source_url": "https://example.test/image.png", "local_path": f"card-thumb/{filename}",
        "status": "downloaded", "mime_type": "image/png", "content_hash": content_hash,
    }
    CardAssetRepository(base_dir).upsert_asset(payload)
    path = resolve_data_dir(base_dir) / "assets" / payload["local_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def mark_ready(base_dir, asset_id="asset_test", *, content_hash="hash", local_path=None):
    relative = local_path if local_path is not None else rendering.thumbnail_relative_path(asset_id)
    path = resolve_data_dir(base_dir) / "assets" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"thumbnail")
    CardAssetThumbnailRepository(base_dir).mark_ready(
        asset_id, local_path=relative, width=100, height=50, size_bytes=path.stat().st_size,
        source_content_hash=content_hash,
    )
    return path


@pytest.fixture
def ffmpeg():
    executable = shutil.which("ffmpeg")
    if executable is None:
        pytest.skip("ffmpeg missing")
    return executable


def make_media(ffmpeg, path, *, size="800x400", duration=None):
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"color=c=red:s={size}:r=10"]
    command += ["-t", str(duration), "-pix_fmt", "yuv420p"] if duration is not None else ["-frames:v", "1"]
    subprocess.run([*command, str(path)], check=True, capture_output=True, timeout=30)


@pytest.mark.parametrize("suffix,duration", [("png", None), ("gif", None), ("mp4", 2), ("mp4", 0.5)])
def test_render_media_bounded_and_short_video_fallback(tmp_path, ffmpeg, monkeypatch, suffix, duration):
    source = tmp_path / f"source.{suffix}"
    make_media(ffmpeg, source, duration=duration)
    run = Mock(wraps=rendering.subprocess.run)
    monkeypatch.setattr(rendering.subprocess, "run", run)
    target = tmp_path / "thumbnail.webp"
    result = rendering.render_thumbnail(source, target, kind="video" if duration else "image")
    assert (result.width, result.height) == (640, 320)
    assert result.size_bytes == target.stat().st_size > 0
    if duration == 0.5:
        assert run.call_count == 2
        assert run.call_args_list[0].args[0][6:8] == ["-ss", "1"]
        assert run.call_args_list[1].args[0][6:8] == ["-ss", "0"]


def test_render_small_image_is_not_upscaled(tmp_path, ffmpeg):
    source = tmp_path / "small.png"
    make_media(ffmpeg, source, size="100x50")
    result = rendering.render_thumbnail(source, tmp_path / "small.webp", kind="image")
    assert (result.width, result.height) == (100, 50)


def test_render_corrupt_file_reports_error_code(tmp_path, ffmpeg):
    source = tmp_path / "broken.png"
    source.write_bytes(b"not media")
    with pytest.raises(rendering.ThumbnailError) as error:
        rendering.render_thumbnail(source, tmp_path / "broken.webp", kind="image")
    assert error.value.code == "ffmpeg_failed"


@pytest.mark.parametrize("chunk", [b"VP8 ", b"VP8L", b"VP8X"])
def test_webp_dimensions_from_headers(chunk):
    header = bytearray(30)
    header[0:4], header[8:12], header[12:16] = b"RIFF", b"WEBP", chunk
    if chunk == b"VP8 ":
        header[26:28], header[28:30] = (640 | 0xC000).to_bytes(2, "little"), (320 | 0x8000).to_bytes(2, "little")
    elif chunk == b"VP8L":
        header[21:25] = ((639) | (319 << 14)).to_bytes(4, "little")
    else:
        header[24:27], header[27:30] = (639).to_bytes(3, "little"), (319).to_bytes(3, "little")
    assert rendering._webp_dimensions(bytes(header)) == (640, 320)


def test_ensure_writes_ready_file(tmp_path, ffmpeg):
    source = store_asset(tmp_path)
    make_media(ffmpeg, source)
    generation.ensure_card_thumbnails(tmp_path, "card-thumb")
    state = CardAssetThumbnailRepository(tmp_path).get("asset_test")
    assert state["status"] == "ready"
    assert state["source_content_hash"] == "hash"
    assert state["error_code"] is None
    assert (state["width"], state["height"]) == (640, 320)
    assert (resolve_data_dir(tmp_path) / "assets" / state["local_path"]).stat().st_size == state["size_bytes"]


def fake_render(source, target, *, kind):
    target.write_bytes(b"rendered")
    return rendering.RenderedThumbnail(100, 50, 8)


def test_ensure_skips_same_hash_and_regenerates_changed_hash(tmp_path, monkeypatch):
    store_asset(tmp_path).write_bytes(b"image")
    render = Mock(side_effect=fake_render)
    monkeypatch.setattr(generation, "render_thumbnail", render)
    generation.ensure_card_thumbnails(tmp_path, "card-thumb")
    generation.ensure_card_thumbnails(tmp_path, "card-thumb")
    assert render.call_count == 1
    asset_repo = CardAssetRepository(tmp_path)
    asset_repo.upsert_asset({**asset_repo.get_asset("asset_test"), "content_hash": "changed"})
    generation.ensure_card_thumbnails(tmp_path, "card-thumb")
    assert render.call_count == 2
    assert CardAssetThumbnailRepository(tmp_path).get("asset_test")["source_content_hash"] == "changed"


def test_missing_source_records_failed_and_is_not_automatically_retried(tmp_path, monkeypatch):
    source = store_asset(tmp_path)
    generation.ensure_card_thumbnails(tmp_path, "card-thumb")
    state = CardAssetThumbnailRepository(tmp_path).get("asset_test")
    assert state["status"] == "failed"
    assert state["error_code"] == "source_missing"
    assert state["source_content_hash"] == "hash"
    source.write_bytes(b"now available")
    render = Mock(side_effect=fake_render)
    monkeypatch.setattr(generation, "render_thumbnail", render)
    generation.ensure_card_thumbnails(tmp_path, "card-thumb")
    render.assert_not_called()


def test_backfill_preview_is_readonly_and_limit_bounds_apply(tmp_path, monkeypatch, capsys):
    for asset_id in ("asset_a", "asset_b"):
        store_asset(tmp_path, asset_id).write_bytes(b"image")
    database = default_database_path(tmp_path)
    before = database.read_bytes()
    main(["--preview", "--base-dir", str(tmp_path)])
    assert json.loads(capsys.readouterr().out) == {
        "mode": "preview", "pending_image": 2, "pending_video": 0, "ready": 0, "failed": 0, "ready_bytes": 0,
    }
    assert database.read_bytes() == before
    assert not (resolve_data_dir(tmp_path) / "assets" / "_thumbnails").exists()
    monkeypatch.setattr(generation, "render_thumbnail", fake_render)
    main(["--apply", "--limit", "1", "--batch-size", "1", "--base-dir", str(tmp_path)])
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert output[-1] == {"mode": "apply", "ready": 1, "failed": 0}
    repo = CardAssetThumbnailRepository(tmp_path)
    assert repo.get("asset_a")["status"] == "ready"
    assert repo.get("asset_b") is None
    assert output[0]["progress"]["last_asset_id"] == "asset_a"


def test_backfill_failed_requires_explicit_retry(tmp_path, monkeypatch, capsys):
    store_asset(tmp_path).write_bytes(b"image")
    repo = CardAssetThumbnailRepository(tmp_path)
    repo.mark_failed("asset_test", error_code="ffmpeg_failed", source_content_hash="hash")
    render = Mock(side_effect=fake_render)
    monkeypatch.setattr(generation, "render_thumbnail", render)
    main(["--apply", "--base-dir", str(tmp_path)])
    assert json.loads(capsys.readouterr().out) == {"mode": "apply", "ready": 0, "failed": 0}
    render.assert_not_called()
    main(["--apply", "--retry-failed", "--base-dir", str(tmp_path)])
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"mode": "apply", "ready": 1, "failed": 0}
    assert repo.get("asset_test")["status"] == "ready"


def client_for(base_dir):
    app = FastAPI()
    app.state.base_dir = base_dir
    app.include_router(api_router)
    return TestClient(app)


def test_thumbnail_delivery_get_head_and_existing_routes(tmp_path):
    store_asset(tmp_path).write_bytes(b"original")
    thumbnail = mark_ready(tmp_path)
    client = client_for(tmp_path)
    url = "/api/card-preview/assets/asset_test/thumbnail"
    for method in (client.get, client.head):
        response = method(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        assert response.headers["cache-control"] == "private, max-age=604800"
    assert client.get(url).content == thumbnail.read_bytes()
    assert client.head(url).content == b""
    assert client.get("/api/card-preview/assets/asset_test").content == b"original"
    assert client.get("/api/card-preview/card-thumb").json()["card"]["card_id"] == "card-thumb"


@pytest.mark.parametrize("case,status,code", [
    ("inactive", 404, "asset_not_found"), ("absent", 404, "thumbnail_not_found"),
    ("stale", 404, "thumbnail_not_found"), ("outside", 403, "asset_path_forbidden"),
])
def test_thumbnail_delivery_rejections(tmp_path, case, status, code):
    store_asset(tmp_path).write_bytes(b"original")
    if case != "absent":
        mark_ready(tmp_path, content_hash="old" if case == "stale" else "hash",
                   local_path="card-thumb/outside.webp" if case == "outside" else None)
    if case == "inactive":
        with CardRepository(tmp_path).connect() as conn:
            conn.execute("update cards set status='processing' where card_id='card-thumb'")
    response = client_for(tmp_path).get("/api/card-preview/assets/asset_test/thumbnail")
    assert response.status_code == status
    assert response.json()["code"] == code


@pytest.mark.parametrize("state,hash_value", [("ready", "hash"), ("ready", None), ("ready", "stale"), ("failed", "hash")])
def test_list_and_detail_thumbnail_urls(tmp_path, state, hash_value):
    content_hash = None if hash_value is None else "hash"
    store_asset(tmp_path, content_hash=content_hash).write_bytes(b"original")
    if state == "ready":
        mark_ready(tmp_path, content_hash=hash_value)
    else:
        CardAssetThumbnailRepository(tmp_path).mark_failed(
            "asset_test", error_code="ffmpeg_failed", source_content_hash=hash_value,
        )
    client = client_for(tmp_path)
    expected = "/api/card-preview/assets/asset_test/thumbnail" if state == "ready" and hash_value == content_hash else None
    item = client.get("/api/card-preview").json()["items"][0]
    asset = client.get("/api/card-preview/card-thumb").json()["image_assets"][0]
    assert item["cover_thumbnail_url"] == expected
    assert asset["thumbnail_url"] == expected
