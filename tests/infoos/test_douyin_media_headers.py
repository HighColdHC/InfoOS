from __future__ import annotations

from backend.application.enrichment.media import artifacts, douyin
from backend.application.enrichment.media.candidates import MediaCandidate


def test_douyin_direct_download_sends_browser_headers_with_card_referer(monkeypatch, tmp_path):
    seen: list[dict] = []
    card = {
        "card_id": "card_douyin_headers",
        "source_platform": "douyin",
        "source_url": "https://www.douyin.com/video/9000000000000000008",
    }
    candidate = MediaCandidate(
        url="https://v26-web.douyinvod.com/video.mp4", kind="video", source="play_url", platform="douyin",
    )
    monkeypatch.setattr(douyin, "collect_media_candidates", lambda *_: [candidate])
    monkeypatch.setattr(douyin, "existing_douyin_video_blocks", lambda *_: [])
    monkeypatch.setattr(douyin, "download_direct_media_url", lambda url, **kwargs: seen.append(kwargs))
    monkeypatch.setattr(douyin, "download_douyin_with_ytdlp", lambda *_: ([{"block_kind": "video"}], None))

    assert douyin.download_douyin_card_media(card, tmp_path) == [{"block_kind": "video"}]
    assert seen[0]["headers"] == {"User-Agent": "Mozilla/5.0", "Referer": card["source_url"]}


def test_direct_media_download_forwards_custom_headers_and_keeps_default(monkeypatch, tmp_path):
    seen: list[dict] = []

    def fake_open(url, **kwargs):
        seen.append(kwargs["headers"])
        return None

    monkeypatch.setattr(artifacts, "_open_public_url", fake_open)
    custom = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"}

    artifacts.download_direct_media_url("https://example.com/a.mp4", assets_dir=tmp_path, index=1, headers=custom)
    artifacts.download_direct_media_url("https://example.com/a.mp4", assets_dir=tmp_path, index=1)

    assert seen == [custom, {"User-Agent": "InfoOS/0.1 (+https://local.infoos)"}]
