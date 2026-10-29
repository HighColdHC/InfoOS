from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.application.enrichment.media import artifacts
from backend.application.enrichment.media.download import download_card_media
from backend.application.enrichment.media.tooling import DownloadedMediaFile, MediaToolResult
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.infrastructure.storage.repositories import CardAssetRepository, CardRepository

MINIMAL_JPEG = b"\xff\xd8\xff\xe0image"
MINIMAL_MP4 = b"\x00\x00\x00\x18ftypmp42video"


@pytest.fixture(autouse=True)
def _public_media_dns(monkeypatch):
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda _host, port, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", port))],
    )


def _card(tmp_path: Path, *, card_id: str, platform: str, source_url: str, raw_payload: dict) -> None:
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": card_id,
            "source_id": None,
            "source_platform": platform,
            "source_type": "video" if platform in {"youtube", "douyin"} else "social_post",
            "source_url": source_url,
            "source_author": "author",
            "title": card_id,
            "content": "content",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": raw_payload,
        }
    )


def test_rss_direct_image_acceptance(monkeypatch, tmp_path):
    _card(
        tmp_path,
        card_id="card_rss_acceptance",
        platform="rss",
        source_url="https://example.com/article",
        raw_payload={"image_url": "https://cdn.example.com/image.jpg"},
    )

    class ImageResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield MINIMAL_JPEG

        def close(self):
            pass

    monkeypatch.setattr(artifacts.requests, "get", lambda *args, **kwargs: ImageResponse())

    download_card_media("card_rss_acceptance", tmp_path)

    assets = CardAssetRepository(tmp_path).list_for_card("card_rss_acceptance")
    assert [(asset["asset_kind"], asset["status"]) for asset in assets] == [("image", "downloaded")]


@pytest.mark.no_storage_bootstrap
def test_media_download_honors_infoos_data_home_for_default_base_dir(monkeypatch, tmp_path):
    data_home = tmp_path / "infoos-data-home"
    card_id = "card_env_acceptance"
    repo_local_card_dir = Path("data") / "assets" / card_id
    if repo_local_card_dir.exists():
        shutil.rmtree(repo_local_card_dir)
    monkeypatch.setenv("INFOOS_DATA_HOME", str(data_home))
    assert initialize_runtime_storage(".") == data_home / "infoos.sqlite3"
    _card(
        Path("."),
        card_id=card_id,
        platform="rss",
        source_url="https://example.com/article",
        raw_payload={"image_url": "https://cdn.example.com/env-image.jpg"},
    )

    class ImageResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield MINIMAL_JPEG

        def close(self):
            pass

    monkeypatch.setattr(artifacts.requests, "get", lambda *args, **kwargs: ImageResponse())

    download_card_media(card_id, Path("."))

    asset = CardAssetRepository(Path(".")).list_for_card(card_id)[0]
    assert (data_home / "assets" / asset["local_path"]).exists()
    assert not repo_local_card_dir.exists()


def test_youtube_yt_dlp_acceptance(monkeypatch, tmp_path):
    _card(
        tmp_path,
        card_id="card_youtube_acceptance",
        platform="youtube",
        source_url="https://www.youtube.com/watch?v=abc123",
        raw_payload={},
    )

    profile_dir = tmp_path / "chrome-profile"
    monkeypatch.setattr(
        "backend.application.enrichment.media.youtube.chrome_profile_dir",
        lambda: profile_dir,
    )

    def fake_run(url, output_dir, *, cookies_from_browser=None):
        assert url == "https://www.youtube.com/watch?v=abc123"
        assert cookies_from_browser == f"chrome:{profile_dir}"
        video_path = output_dir / "abc123.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(MINIMAL_MP4)
        return MediaToolResult(
            status="downloaded",
            files=[
                DownloadedMediaFile(
                    video_path,
                    "card_youtube_acceptance/youtube/abc123.mp4",
                    "video",
                    "video/mp4",
                    video_path.stat().st_size,
                )
            ],
            metadata={
                "info": {
                    "id": "abc123",
                    "extractor": "youtube",
                    "extractor_key": "Youtube",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123",
                    "uploader": "acceptance-channel",
                    "timestamp": 1,
                },
                "info_json_sha256": "fixture-info-sha256",
                "version": "fixture-yt-dlp",
            },
        )

    monkeypatch.setattr("backend.application.enrichment.media.youtube.run_yt_dlp_download", fake_run)

    download_card_media("card_youtube_acceptance", tmp_path)

    asset = CardAssetRepository(tmp_path).list_for_card("card_youtube_acceptance")[0]
    assert asset["asset_kind"] == "video"
    assert asset["raw_payload"]["strategy"] == "yt_dlp"


def test_reddit_gallery_acceptance(monkeypatch, tmp_path):
    _card(
        tmp_path,
        card_id="card_reddit_acceptance",
        platform="reddit",
        source_url="https://www.reddit.com/gallery/example",
        raw_payload={},
    )

    def fake_run(url, output_dir, *, cookies_from_browser=None):
        image_path = output_dir / "gallery.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(MINIMAL_JPEG)
        return MediaToolResult(
            status="downloaded",
            files=[
                DownloadedMediaFile(
                    image_path,
                    "card_reddit_acceptance/gallery/gallery.jpg",
                    "image",
                    "image/jpeg",
                    image_path.stat().st_size,
                )
            ],
        )

    monkeypatch.setattr("backend.application.enrichment.media.gallery.run_gallery_dl_download", fake_run)

    download_card_media("card_reddit_acceptance", tmp_path)

    asset = CardAssetRepository(tmp_path).list_for_card("card_reddit_acceptance")[0]
    assert asset["asset_kind"] == "image"
    assert asset["raw_payload"]["strategy"] == "gallery_dl"


def test_douyin_direct_play_url_acceptance(monkeypatch, tmp_path):
    _card(
        tmp_path,
        card_id="card_douyin_acceptance",
        platform="douyin",
        source_url="https://www.douyin.com/video/123",
        raw_payload={"play_url": "https://v.douyin.example/video.mp4"},
    )

    class VideoResponse:
        status_code = 200
        headers = {"content-type": "video/mp4"}
        content = MINIMAL_MP4

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield self.content

        def close(self):
            return None

    monkeypatch.setattr(artifacts.requests, "get", lambda *args, **kwargs: VideoResponse())

    download_card_media("card_douyin_acceptance", tmp_path)

    asset = CardAssetRepository(tmp_path).list_for_card("card_douyin_acceptance")[0]
    assert asset["asset_kind"] == "video"
    assert asset["raw_payload"]["strategy"] == "direct_candidate"


def test_xiaohongshu_opencli_download_acceptance(monkeypatch, tmp_path):
    _card(
        tmp_path,
        card_id="card_xhs_acceptance",
        platform="xiaohongshu",
        source_url="https://www.xiaohongshu.com/search_result/1?xsec_token=abc",
        raw_payload={},
    )

    def fake_run(command, **kwargs):
        output_dir = Path(command[command.index("--output") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "image_1.jpg").write_bytes(MINIMAL_JPEG)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr("backend.application.enrichment.media.xiaohongshu.subprocess.run", fake_run)

    download_card_media("card_xhs_acceptance", tmp_path)

    asset = CardAssetRepository(tmp_path).list_for_card("card_xhs_acceptance")[0]
    assert asset["asset_kind"] == "image"
    assert asset["status"] == "downloaded"
