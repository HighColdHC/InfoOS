"""Media enrichment tests — X (Twitter) content extraction and derived blocks."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
    SourceRepository,
)


# ── Image understanding tests ──────────────────────────────────────────

from tests.infoos._factories import setup_card_and_asset  # noqa: F401

MINIMAL_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x01\x00\x01\x00\x00\xff\xd9"
MINIMAL_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def _allow_public_media_dns(monkeypatch):
    import importlib

    artifacts = importlib.import_module("backend.application.enrichment.media.artifacts")

    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda _hostname, port, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    return artifacts


def test_download_x_card_media_does_not_adopt_unpersisted_durable_file(tmp_path, monkeypatch):
    from backend.application.enrichment.media import download

    card_id = "card_test_media"
    source_url = "https://x.com/test/status/123"
    base_dir = tmp_path

    # Create a fake downloaded file
    assets_dir = base_dir / "data" / "assets" / card_id / "tweets"
    assets_dir.mkdir(parents=True)
    img_path = assets_dir / "tweet_1.jpg"
    img_path.write_bytes(MINIMAL_JPEG_BYTES)

    # Mock repository at source location (infoos.storage.repositories)
    assets = []
    blocks = []

    class FakeAssetRepo:
        def list_for_card(self, _card_id):
            return []

        def upsert_asset(self, payload):
            assets.append(dict(payload))

    class FakeBlockRepo:
        def list_for_card(self, _card_id):
            return []

        def upsert_block(self, payload):
            blocks.append(dict(payload))

    # Patch at the Module level where the classes are defined
    import backend.infrastructure.storage.repositories as repos_module
    monkeypatch.setattr(
        repos_module, "CardAssetRepository",
        lambda *a, **kw: FakeAssetRepo(),
    )
    monkeypatch.setattr(
        repos_module, "CardContentBlockRepository",
        lambda *a, **kw: FakeBlockRepo(),
    )

    monkeypatch.setattr(download, "run_x_media_download_lifecycle", lambda **_kwargs: SimpleNamespace(
        command_result=None,
        failure_reason="x_media_download_failed",
        details={"download_attempt_count": 0},
        attempt_dir=None,
    ))
    monkeypatch.setattr(download, "run_x_yt_dlp_fallback", lambda *_args, **_kwargs: SimpleNamespace(
        status="failed", files=[], provenance={}, evidence={"reason": "failed"}
    ))

    result = download.download_x_card_media(card_id, source_url, base_dir)

    assert assets == []
    assert result == []
    assert all(block["block_kind"] != "image" for block in blocks)


def test_download_card_media_downloads_generic_image_url(tmp_path, monkeypatch):
    from backend.application.enrichment.media.download import download_card_media

    artifacts = _allow_public_media_dns(monkeypatch)

    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "AI Feed",
            "cadence_hours": 24.0,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_ai",
        }
    )
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_rss_media",
            "source_id": "rss_ai",
            "source_platform": "rss",
            "source_type": "article",
            "source_url": "https://example.com/post",
            "title": "RSS card with media",
            "content": "Body",
            "summary": "",
            "captured_at": "2026-06-23T00:00:00Z",
            "processed_at": "2026-06-23T00:00:00Z",
            "raw_payload": {"image_url": "https://cdn.example.com/hero.jpg"},
        }
    )

    class ImageResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}
        content = MINIMAL_JPEG_BYTES

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield self.content

        def close(self):
            return None

    monkeypatch.setattr(artifacts.requests, "get", lambda *args, **kwargs: ImageResponse())

    result = download_card_media("card_rss_media", tmp_path)

    assert len(result) == 1
    asset = CardAssetRepository(tmp_path).list_for_card("card_rss_media")[0]
    assert asset["asset_kind"] == "image"
    assert asset["status"] == "downloaded"
    assert asset["remote_url"] == "https://cdn.example.com/hero.jpg"
    assert (tmp_path / "data" / "assets" / asset["local_path"]).exists()
    block = CardContentBlockRepository(tmp_path).list_for_card("card_rss_media")[0]
    assert block["block_kind"] == "image"
    assert block["source_url"] == "https://cdn.example.com/hero.jpg"


def test_download_card_media_resolves_url_type_media_payloads_without_extensions(tmp_path, monkeypatch):
    from backend.application.enrichment.media.download import download_card_media

    artifacts = _allow_public_media_dns(monkeypatch)

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_generic_media",
            "source_id": None,
            "source_platform": "rss",
            "source_type": "article",
            "source_url": "https://example.com/article-with-media",
            "title": "Video with media payload",
            "content": "Body",
            "summary": "",
            "captured_at": "2026-07-06T00:00:00Z",
            "processed_at": "2026-07-06T00:00:00Z",
            "raw_payload": {
                "thumbnail": {
                    "url": "https://i.ytimg.com/vi/abc/default",
                    "type": "image/jpeg",
                },
            },
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_youtube_enclosure",
            "card_id": "card_generic_media",
            "position": 1,
            "layer": "source",
            "block_kind": "video",
            "status": "enriched",
            "body": "",
            "source_url": "https://cdn.example.com/youtube/video-stream",
            "provenance": "test",
            "raw_payload": {
                "enclosures": [
                    {
                        "url": "https://cdn.example.com/youtube/video-stream",
                        "type": "video/mp4",
                    }
                ],
            },
        }
    )

    class MediaResponse:
        def __init__(self, content: bytes, content_type: str):
            self.status_code = 200
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield self.content

        def close(self):
            return None

    def fake_get(url, **kwargs):
        if "ytimg" in url:
            return MediaResponse(MINIMAL_JPEG_BYTES, "image/jpeg")
        return MediaResponse(MINIMAL_MP4_BYTES, "video/mp4")

    monkeypatch.setattr(artifacts.requests, "get", fake_get)

    result = download_card_media("card_generic_media", tmp_path)

    assert {block["block_kind"] for block in result} == {"image", "video"}
    assets = CardAssetRepository(tmp_path).list_for_card("card_generic_media")
    assert {asset["asset_kind"] for asset in assets} == {"image", "video"}
    assert {asset["remote_url"] for asset in assets} == {
        "https://i.ytimg.com/vi/abc/default",
        "https://cdn.example.com/youtube/video-stream",
    }
    for asset in assets:
        assert (tmp_path / "data" / "assets" / asset["local_path"]).exists()


def test_download_x_card_media_falls_back_to_resolved_payload_media_when_opencli_fails(tmp_path, monkeypatch):
    from backend.application.enrichment.media.download import download_card_media

    artifacts = _allow_public_media_dns(monkeypatch)

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_x_payload_media",
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/test/status/123",
            "title": "X card with payload media",
            "content": "Body",
            "summary": "",
            "captured_at": "2026-07-06T00:00:00Z",
            "processed_at": "2026-07-06T00:00:00Z",
            "raw_payload": {
                "opencli_record": {
                    "id_str": "123",
                    "media": [
                        {
                            "url": "https://pbs.twimg.com/media/public?format=jpg",
                            "type": "photo",
                        }
                    ],
                }
            },
        }
    )

    monkeypatch.setattr(
        "backend.application.enrichment.media.download.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="AUTH_REQUIRED"),
    )

    class ImageResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield MINIMAL_JPEG_BYTES

        def close(self):
            return None

    monkeypatch.setattr(artifacts.requests, "get", lambda *args, **kwargs: ImageResponse())

    result = download_card_media("card_x_payload_media", tmp_path)

    assert [block["block_kind"] for block in result] == ["image"]
    assets = CardAssetRepository(tmp_path).list_for_card("card_x_payload_media")
    assert len(assets) == 1
    assert assets[0]["remote_url"] == "https://pbs.twimg.com/media/public?format=jpg"
    stored_blocks = CardContentBlockRepository(tmp_path).list_for_card("card_x_payload_media")
    assert [block["block_kind"] for block in stored_blocks] == ["image"]
