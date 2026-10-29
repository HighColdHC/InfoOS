from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.domain.sources.douyin import DouyinItemIdentity, extract_single_douyin_url
from backend.infrastructure.connectors.douyin import item as item_module
from backend.infrastructure.connectors.douyin.item import DouyinItemConnector
from backend.infrastructure.public_http import PublicHttpHop, ResolvedPublicUrl


AWEME_ID = "9000000000000000005"
CANONICAL = f"https://www.douyin.com/video/{AWEME_ID}"


def test_share_text_requires_one_known_standard_port_url():
    assert extract_single_douyin_url(f"复制打开抖音 {CANONICAL}?foo=secret#bar") == f"{CANONICAL}?foo=secret#bar"
    with pytest.raises(ValueError, match="exactly one"):
        extract_single_douyin_url(f"{CANONICAL} https://v.douyin.com/abc/")
    with pytest.raises(ValueError, match="unsupported"):
        extract_single_douyin_url("https://evil.example/video/9000000000000000005")
    with pytest.raises(ValueError, match="unsupported"):
        extract_single_douyin_url("https://www.douyin.com:8443/video/9000000000000000005")


def test_short_url_uses_safe_terminal_redirect_without_fetching_terminal(monkeypatch):
    captured = {}

    def fake_resolve(url, **kwargs):
        captured.update({"url": url, **kwargs})
        terminal = "https://www.iesdouyin.com/share/video/9000000000000000004/?region=CN#secret"
        assert kwargs["terminal_url"](terminal) is True
        return ResolvedPublicUrl(terminal, (PublicHttpHop(url, 302, terminal),))

    monkeypatch.setattr(item_module, "resolve_public_url", fake_resolve)

    identity = item_module.resolve_douyin_item_identity("复制 https://v.douyin.com/qpMfgM5vbuY/?x=secret")

    assert identity.canonical_url == "https://www.douyin.com/video/9000000000000000004"
    assert captured["max_redirects"] == 5


def test_item_connector_validates_and_maps_ytdlp_metadata(monkeypatch):
    metadata = {
        "id": AWEME_ID,
        "extractor": "Douyin",
        "extractor_key": "Douyin",
        "webpage_url": f"{CANONICAL}?previous_page=app_code_link",
        "title": "作品标题",
        "description": "作品正文",
        "uploader": "作者",
        "uploader_id": "creator-1",
        "timestamp": 1784592000,
        "duration": 12.5,
        "formats": [{"format_id": "h264", "url": "https://video.example/a.mp4?token=secret", "height": 720}],
        "thumbnails": [
            {"id": "cover", "url": "https://image.example/a.jpg?token=one"},
            {"id": "duplicate", "url": "https://image.example/a.jpg?token=two"},
        ],
    }
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(metadata), stderr="cookie secret")

    monkeypatch.setattr(item_module.shutil, "which", lambda _name: "/opt/bin/yt-dlp")
    monkeypatch.setattr(item_module.subprocess, "run", fake_run)
    monkeypatch.setattr(item_module, "chrome_profile_dir", lambda: "/tmp/infoos-profile")
    connector = DouyinItemConnector("douyin_item", CANONICAL)

    result = connector.fetch_new_items(None)

    assert len(result.items) == 1
    raw = result.items[0]
    assert raw.raw_item_id == CANONICAL
    assert raw.source_url == CANONICAL
    assert raw.raw_text == "作品正文"
    assert raw.raw_author.name == "作者"
    assert raw.raw_metadata["formats"][0]["url"] == "https://video.example/a.mp4"
    assert raw.raw_metadata["thumbnails"] == [{"id": "cover", "url": "https://image.example/a.jpg"}]
    assert commands[0][-1] == CANONICAL
    assert commands[0][commands[0].index("--cookies-from-browser") + 1] == "chrome:/tmp/infoos-profile"


def test_item_connector_rejects_mismatched_ytdlp_identity(monkeypatch):
    metadata = {"id": "1", "extractor": "Douyin", "webpage_url": "https://www.douyin.com/video/1"}
    monkeypatch.setattr(item_module.shutil, "which", lambda _name: "/opt/bin/yt-dlp")
    monkeypatch.setattr(
        item_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(metadata), stderr=""),
    )

    with pytest.raises(RuntimeError, match="mismatched"):
        item_module.load_douyin_item_metadata(DouyinItemIdentity(AWEME_ID))
