from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from backend.application.enrichment.media import artifacts, douyin as douyin_module, douyin_ytdlp
from backend.application.enrichment.media.download import download_card_media
from backend.application.enrichment.media.tooling import DownloadedMediaFile, MediaToolResult
from backend.application.tasks.handlers.media_download import handle_media_download
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository

MINIMAL_MP4 = b"\x00\x00\x00\x18ftypmp42video"


@pytest.fixture(autouse=True)
def _public_media_dns(monkeypatch):
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda _host, port, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", port))],
    )


def test_douyin_downloads_play_url_as_video(monkeypatch, tmp_path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_douyin_media",
            "source_id": None,
            "source_platform": "douyin",
            "source_type": "video",
            "source_url": "https://www.douyin.com/video/123",
            "source_author": "author",
            "title": "抖音视频",
            "content": "desc",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {"play_url": "https://v.douyin.example/video.mp4"},
        }
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

    blocks = download_card_media("card_douyin_media", tmp_path)

    assert [block["block_kind"] for block in blocks] == ["video"]
    asset = CardAssetRepository(tmp_path).list_for_card("card_douyin_media")[0]
    assert asset["asset_kind"] == "video"
    assert asset["raw_payload"]["strategy"] == "direct_candidate"


def test_douyin_parses_pretty_opencli_tab_json() -> None:
    stdout = '{\n  "page": "CCA87B18E1CB4B30C57E56325BBD94DA",\n  "url": "https://www.douyin.com/video/123"\n}\n'

    assert douyin_module._parse_opencli_target_id(stdout) == "CCA87B18E1CB4B30C57E56325BBD94DA"


def test_douyin_downloads_browser_resource_pair_as_merged_video(monkeypatch, tmp_path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_douyin_browser_media",
            "source_id": None,
            "source_platform": "douyin",
            "source_type": "video",
            "source_url": "https://www.douyin.com/video/9000000000000000007",
            "source_author": "author",
            "title": "抖音视频",
            "content": "desc",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {"opencli_record": {"url": "https://www.douyin.com/video/9000000000000000007"}},
        }
    )
    browser_payload = {
        "url": "https://www.douyin.com/video/9000000000000000007",
        "videos": [{"currentSrc": "blob:https://www.douyin.com/blob-id", "readyState": 4}],
        "entries": [
            "https://v26-web.douyinvod.com/path/media-video-hvc1/?mime_type=video_mp4",
            "https://v26-web.douyinvod.com/path/media-audio-und-mp4a/?mime_type=video_mp4",
        ],
    }
    commands: list[list[str]] = []

    class Response:
        status_code = 200

        def __init__(self, content: bytes):
            self.content = content
            self.headers = {"content-type": "video/mp4"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield self.content

        def close(self):
            pass

    def fake_run(command, **kwargs):
        commands.append([str(part) for part in command])
        if command[3:7] == ["browser", "site:douyin", "tab", "new"]:
            return SimpleNamespace(returncode=0, stdout="tab-douyin-1\n", stderr="")
        if command[3:5] == ["browser", "site:douyin"] and command[5] == "wait":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[3:5] == ["browser", "site:douyin"] and command[5] == "eval":
            return SimpleNamespace(returncode=0, stdout=douyin_module.json.dumps(browser_payload), stderr="")
        if command[3:7] == ["browser", "site:douyin", "tab", "close"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if Path(command[0]).name == "ffmpeg":
            output_path = Path(command[-1])
            output_path.write_bytes(MINIMAL_MP4)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def fake_get(url, **kwargs):
        if "media-video" in url:
            return Response(MINIMAL_MP4)
        if "media-audio" in url:
            return Response(b"\x00\x00\x00\x18ftypM4A audio")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(douyin_module, "opencli_tab_lease", lambda _site: nullcontext())
    monkeypatch.setattr(douyin_module, "resolve_opencli_executable", lambda executable="opencli": "opencli", raising=False)
    monkeypatch.setattr(douyin_module, "subprocess", SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired), raising=False)
    monkeypatch.setattr(artifacts.requests, "get", fake_get)
    monkeypatch.setattr(douyin_module.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)

    blocks = download_card_media("card_douyin_browser_media", tmp_path)

    assert [block["block_kind"] for block in blocks] == ["video"]
    asset = CardAssetRepository(tmp_path).list_for_card("card_douyin_browser_media")[0]
    assert asset["asset_kind"] == "video"
    assert asset["raw_payload"]["strategy"] == "browser_resource_pair"
    assert any(command[:6] == ["opencli", "--profile", "infoos-fetch", "browser", "site:douyin", "eval"] for command in commands)
    assert any(command[:7] == ["opencli", "--profile", "infoos-fetch", "browser", "site:douyin", "tab", "close"] for command in commands)


def test_douyin_falls_back_to_browser_resources_when_direct_candidate_fails(monkeypatch, tmp_path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_douyin_direct_stale",
            "source_id": None,
            "source_platform": "douyin",
            "source_type": "video",
            "source_url": "https://www.douyin.com/video/9000000000000000007",
            "source_author": "author",
            "title": "抖音视频",
            "content": "desc",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {"play_url": "https://stale.douyin.example/video.mp4"},
        }
    )

    monkeypatch.setattr(artifacts.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("stale")))
    monkeypatch.setattr(douyin_module, "_download_browser_resource_pair", lambda *args, **kwargs: douyin_module.DouyinDownloadAttempt(downloaded={
        "path": tmp_path / "data" / "assets" / "card_douyin_direct_stale" / "douyin" / "media_1_browser.mp4",
        "kind": "video",
        "mime_type": "video/mp4",
        "size_bytes": len(MINIMAL_MP4),
        "content_hash": "a" * 64,
        "strategy": "browser_resource_pair",
        "remote_url": "https://v26-web.douyinvod.com/video",
    }))
    browser_path = tmp_path / "data" / "assets" / "card_douyin_direct_stale" / "douyin" / "media_1_browser.mp4"
    browser_path.parent.mkdir(parents=True)
    browser_path.write_bytes(MINIMAL_MP4)

    blocks = download_card_media("card_douyin_direct_stale", tmp_path)

    assert [block["block_kind"] for block in blocks] == ["video"]
    asset = CardAssetRepository(tmp_path).list_for_card("card_douyin_direct_stale")[0]
    assert asset["raw_payload"]["strategy"] == "browser_resource_pair"


def test_douyin_browser_tab_without_target_id_records_gap_without_eval(monkeypatch, tmp_path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_douyin_bad_tab",
            "source_id": None,
            "source_platform": "douyin",
            "source_type": "video",
            "source_url": "https://www.douyin.com/video/9000000000000000007",
            "source_author": "author",
            "title": "抖音视频",
            "content": "desc",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {},
        }
    )
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append([str(part) for part in command])
        if command[3:7] == ["browser", "site:douyin", "tab", "new"]:
            return SimpleNamespace(returncode=0, stdout="Update available: v1.8.5 -> v1.8.6\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(douyin_module, "opencli_tab_lease", lambda _site: nullcontext())
    monkeypatch.setattr(douyin_module, "resolve_opencli_executable", lambda executable="opencli": "opencli", raising=False)
    monkeypatch.setattr(douyin_module, "subprocess", SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired), raising=False)

    blocks = download_card_media("card_douyin_bad_tab", tmp_path)

    assert blocks == []
    assert not any(command[:6] == ["opencli", "--profile", "infoos-fetch", "browser", "site:douyin", "eval"] for command in commands)
    gap = next(block for block in CardContentBlockRepository(tmp_path).list_for_card("card_douyin_bad_tab") if block["block_kind"] == "media_download_gap")
    assert gap["raw_payload"]["reason"] == "douyin_browser_target_id_missing"


def test_douyin_partial_files_are_removed_when_audio_download_fails(monkeypatch, tmp_path):
    class Response:
        status_code = 200
        headers = {"content-type": "video/mp4"}

        def __init__(self, content: bytes):
            self._content = content

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=1024 * 1024):
            yield self._content

        def close(self):
            pass

    def fake_get(url, **kwargs):
        if "video" in url:
            return Response(MINIMAL_MP4)
        raise OSError("audio failed")

    monkeypatch.setattr(artifacts.requests, "get", fake_get)
    monkeypatch.setattr(douyin_module.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)

    result = douyin_module._download_and_merge_resource_pair("https://douyin.example/video", "https://douyin.example/audio", assets_dir=tmp_path)

    assert result.downloaded is None
    assert result.reason == "douyin_audio_download_failed"
    assert not (tmp_path / ".partial").exists()


def test_douyin_resource_download_streams_without_response_content(monkeypatch, tmp_path):
    class StreamingResponse:
        status_code = 200
        headers = {"content-type": "video/mp4"}

        def close(self):
            return None

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=1024 * 1024):
            yield b"\x00\x00\x00\x18"
            yield b"ftypmp42video"

        def close(self):
            pass

    monkeypatch.setattr(artifacts.requests, "get", lambda *args, **kwargs: StreamingResponse())

    path = tmp_path / "streamed.mp4"

    assert douyin_module._download_resource_to_path("https://douyin.example/video", path) is True
    assert path.read_bytes() == MINIMAL_MP4


def test_douyin_resource_download_allows_bounded_video_larger_than_generic_limit(monkeypatch, tmp_path):
    requested = {}

    def fake_download(url, **kwargs):
        requested.update({"url": url, **kwargs})
        kwargs["destination"].write_bytes(MINIMAL_MP4)
        return True

    monkeypatch.setattr(douyin_module, "download_public_url_to_path", fake_download)

    path = tmp_path / "large-video.mp4"

    assert douyin_module._download_resource_to_path("https://douyin.example/large-video", path) is True
    assert requested["max_bytes"] == 256 * 1024 * 1024


def test_douyin_browser_resources_reject_mismatched_final_item(monkeypatch):
    payload = {
        "url": "https://www.douyin.com/video/222?token=secret",
        "entries": ["https://v26-web.douyinvod.com/media-video/?token=secret"],
    }

    def fake_run(command, **_kwargs):
        if command[3:7] == ["browser", "site:douyin", "tab", "new"]:
            return SimpleNamespace(returncode=0, stdout="tab-1", stderr="")
        if command[5] == "eval":
            return SimpleNamespace(returncode=0, stdout=douyin_module.json.dumps(payload), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(douyin_module, "opencli_tab_lease", lambda _site: nullcontext())
    monkeypatch.setattr(douyin_module, "resolve_opencli_executable", lambda _name="opencli": "opencli")
    monkeypatch.setattr(
        douyin_module,
        "subprocess",
        SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
    )

    pair, reason, details = douyin_module._extract_douyin_resource_pair_from_browser(
        "https://www.douyin.com/video/111?from=card"
    )

    assert pair is None
    assert reason == "douyin_browser_identity_mismatch"
    assert details == {
        "expected_url": "https://www.douyin.com/video/111",
        "actual_url": "https://www.douyin.com/video/222",
    }


def test_douyin_diagnostic_strips_secrets_and_url_queries():
    diagnostic = douyin_module._safe_diagnostic(
        "Cookie: sid=private\nfailed https://cdn.example/video?token=private&x=1"
    )

    assert "private" not in diagnostic
    assert "Cookie:" not in diagnostic
    assert diagnostic.endswith("https://cdn.example/video")


def test_douyin_ytdlp_media_is_idempotent_via_asset_link(monkeypatch, tmp_path):
    aweme_id = "9000000000000000005"
    canonical = f"https://www.douyin.com/video/{aweme_id}"
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_douyin_ytdlp_idempotent",
            "source_id": None,
            "source_platform": "douyin",
            "source_type": "item",
            "source_url": canonical,
            "source_author": "author",
            "title": "抖音视频",
            "content": "desc",
            "captured_at": "2026-07-21T00:00:00Z",
            "processed_at": "2026-07-21T00:00:00Z",
            "raw_payload": {},
        }
    )
    calls = []

    def fake_ytdlp(_url, output_dir, **_kwargs):
        calls.append(output_dir)
        path = output_dir / f"{aweme_id}.mp4"
        path.write_bytes(MINIMAL_MP4)
        return MediaToolResult(
            status="downloaded",
            files=[DownloadedMediaFile(path, path.name, "video", "video/mp4", path.stat().st_size)],
            metadata={
                "info": {
                    "id": aweme_id,
                    "extractor": "Douyin",
                    "extractor_key": "Douyin",
                    "webpage_url": canonical,
                },
                "info_json_sha256": "a" * 64,
            },
        )

    monkeypatch.setattr(douyin_ytdlp, "run_yt_dlp_download", fake_ytdlp)
    monkeypatch.setattr(douyin_module, "_download_browser_resource_pair", lambda *_args, **_kwargs: pytest.fail("browser fallback should not run"))

    first = download_card_media("card_douyin_ytdlp_idempotent", tmp_path)
    second = download_card_media("card_douyin_ytdlp_idempotent", tmp_path)

    assert len(calls) == 1
    assert [block["block_kind"] for block in first] == ["video"]
    assert [block["block_kind"] for block in second] == ["video"]
    assert first[0]["raw_payload"]["asset_id"]
    assert second[0]["raw_payload"]["asset_id"] == first[0]["raw_payload"]["asset_id"]


def test_douyin_media_task_rejects_stale_missing_video_asset(monkeypatch, tmp_path):
    card_id = "card_douyin_stale_asset"
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": card_id,
            "source_id": None,
            "source_platform": "douyin",
            "source_type": "item",
            "source_url": "https://www.douyin.com/video/123",
            "title": "抖音视频",
            "content": "desc",
            "captured_at": "2026-07-21T00:00:00Z",
            "processed_at": "2026-07-21T00:00:00Z",
            "raw_payload": {},
        }
    )
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "asset_stale_video",
            "card_id": card_id,
            "asset_kind": "video",
            "source_url": "https://www.douyin.com/video/123",
            "local_path": f"{card_id}/missing.mp4",
            "mime_type": "video/mp4",
            "size_bytes": 99,
            "content_hash": "sha256:" + "b" * 64,
            "preservation_class": "cached",
            "status": "downloaded",
            "cleanup_state": "present",
            "raw_payload": {},
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "gap_stale_video",
            "card_id": card_id,
            "position": 1,
            "layer": "media",
            "block_kind": "media_download_gap",
            "status": "blocked",
            "body": "missing",
            "source_url": "https://www.douyin.com/video/123",
            "provenance": "test",
            "raw_payload": {"reason": "douyin_ytdlp_download_failed"},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.download_card_media",
        lambda *_args, **_kwargs: [],
    )

    result = handle_media_download(
        Task("media_stale", TaskType.MEDIA_DOWNLOAD, "card", card_id),
        tmp_path,
    )

    assert result["status"] == TaskStatus.FAILED.value
    assert result["artifacts"]["media_available"] is False
    assert result["artifacts"]["download_status"] == "failed_download_error"
    gap = CardContentBlockRepository(tmp_path).list_for_card(card_id)[0]
    assert gap["status"] == "blocked"


def test_douyin_media_task_reuses_valid_understood_video(monkeypatch, tmp_path):
    card_id = "card_douyin_understood_asset"
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": card_id,
            "source_id": None,
            "source_platform": "douyin",
            "source_type": "item",
            "source_url": "https://www.douyin.com/video/456",
            "title": "抖音视频",
            "content": "desc",
            "captured_at": "2026-07-21T00:00:00Z",
            "processed_at": "2026-07-21T00:00:00Z",
            "raw_payload": {},
        }
    )
    local_path = f"{card_id}/douyin/understood.mp4"
    absolute_path = tmp_path / "data" / "assets" / local_path
    absolute_path.parent.mkdir(parents=True)
    absolute_path.write_bytes(MINIMAL_MP4)
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "asset_understood_video",
            "card_id": card_id,
            "asset_kind": "video",
            "source_url": "https://www.douyin.com/video/456",
            "local_path": local_path,
            "mime_type": "video/mp4",
            "size_bytes": len(MINIMAL_MP4),
            "content_hash": "sha256:" + "c" * 64,
            "preservation_class": "cached",
            "status": "understood",
            "cleanup_state": "present",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.download_card_media",
        lambda *_args, **_kwargs: [],
    )

    result = handle_media_download(
        Task("media_understood", TaskType.MEDIA_DOWNLOAD, "card", card_id),
        tmp_path,
    )

    assert result["status"] == TaskStatus.SUCCESS.value
    assert result["artifacts"]["media_available"] is True
    assert result["artifacts"]["download_status"] == "downloaded"
    assert result["artifacts"]["assets_downloaded"] == 0
    assert result["artifacts"]["assets"][0]["status"] == "understood"


def test_douyin_media_task_replaces_legacy_stream_fields_with_safe_summary(monkeypatch, tmp_path):
    card_id = "card_douyin_opencli_error"
    source_url = "https://www.douyin.com/video/789"
    CardRepository(tmp_path).upsert_card({
        "card_id": card_id, "source_platform": "douyin", "source_type": "item",
        "source_url": source_url, "title": "video", "content": "body",
        "captured_at": "2026-09-25T00:00:00Z", "processed_at": "2026-09-25T00:00:00Z",
        "raw_payload": {},
    })

    def failed_download(_card_id, _base_dir):
        CardContentBlockRepository(tmp_path).upsert_block({
            "block_id": f"block_{card_id}_media_download_gap", "card_id": card_id,
            "position": 1, "layer": "media", "block_kind": "media_download_gap",
            "status": "blocked", "body": "failed", "source_url": source_url,
            "raw_payload": {
                "reason": "douyin_browser_eval_failed",
                "details": {"strategy": "browser_resource", "stderr": "Cookie: sessionid=private\nuser a@b.example\nhttps://api.example/data?cursor=secret"},
            },
        })
        return []

    monkeypatch.setattr("backend.application.enrichment.media.download.download_card_media", failed_download)
    result = handle_media_download(Task("media_douyin_error", TaskType.MEDIA_DOWNLOAD, "card", card_id), tmp_path)
    gap = next(block for block in CardContentBlockRepository(tmp_path).list_for_card(card_id)
               if block["block_kind"] == "media_download_gap")
    details = gap["raw_payload"]["details"]
    assert "stderr" not in details
    assert result["artifacts"]["stderr_summary"] == details["stderr_summary"]
    assert len(details["stderr_summary"]) <= 800
    for secret in ("private", "a@b.example", "secret"):
        assert secret not in details["stderr_summary"]


def test_media_gap_storage_never_persists_raw_opencli_streams(tmp_path):
    CardRepository(tmp_path).upsert_card({
        "card_id": "card_safe_gap", "source_platform": "douyin", "source_type": "item",
        "source_url": "https://www.douyin.com/video/1", "title": "video", "content": "body",
        "captured_at": "2026-09-25T00:00:00Z", "processed_at": "2026-09-25T00:00:00Z",
        "raw_payload": {},
    })
    repo = CardContentBlockRepository(tmp_path)
    repo.upsert_block({
        "block_id": "gap_safe", "card_id": "card_safe_gap", "position": 1,
        "layer": "media", "block_kind": "media_download_gap", "status": "blocked",
        "body": "failed", "raw_payload": {
            "reason": "douyin_browser_eval_failed",
            "details": {"stderr": "sessionid=private a@b.example https://api.example/?cursor=secret"},
        },
    })
    details = repo.list_for_card("card_safe_gap")[0]["raw_payload"]["details"]
    assert "stderr" not in details
    assert "private" not in details["stderr_summary"]
    assert "a@b.example" not in details["stderr_summary"]
    assert "secret" not in details["stderr_summary"]
