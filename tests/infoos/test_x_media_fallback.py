from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from backend.application.enrichment.media import artifacts, x_downloader
from backend.application.enrichment.media.x_downloader import XFallbackResult
from backend.application.enrichment.media.x_urls import parse_canonical_x_status_url
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository

MINIMAL_JPEG = b"\xff\xd8\xff\xe0image"
MINIMAL_MP4 = b"\x00\x00\x00\x18ftypmp42video"


@pytest.mark.parametrize(
    "value",
    [
        "http://x.com/alice/status/123",
        "https://user@x.com/alice/status/123",
        "https://x.com:444/alice/status/123",
        "https://x.com.evil.example/alice/status/123",
        "https://evilx.com/alice/status/123",
        "https://x.com/alice/status/not-a-number",
        "https://x.com/alice/status/123/extra",
    ],
)
def test_canonical_x_status_parser_rejects_unsafe_or_ambiguous_urls(value):
    assert parse_canonical_x_status_url(value) is None


def test_canonical_x_status_parser_normalizes_hosts_and_drops_query_fragment():
    parsed = parse_canonical_x_status_url("https://www.twitter.com/alice/status/123?utm=x#fragment")
    assert parsed is not None
    assert parsed.tweet_id == "123"
    assert parsed.url == "https://x.com/alice/status/123"


class _Response:
    def __init__(self, *, chunks=(), status_code=200, location=None, content_type="image/jpeg"):
        self._chunks = list(chunks)
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        if location:
            self.headers["location"] = location

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        return None


@pytest.mark.parametrize(
    "location",
    ["http://pbs.twimg.com/media/a.jpg", "https://127.0.0.1/private.jpg", "https://evil.example/a.jpg"],
)
def test_x_direct_download_rejects_unsafe_redirects(monkeypatch, tmp_path, location):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(status_code=302, location=location)

    monkeypatch.setattr(artifacts, "open_pinned_url", get)
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    result = artifacts.download_direct_media_url(
        "https://pbs.twimg.com/media/a.jpg",
        assets_dir=tmp_path,
        index=1,
        allowed_hosts=frozenset({"pbs.twimg.com", "video.twimg.com"}),
    )
    assert result is None
    assert len(calls) == 1


def test_x_direct_download_streams_with_byte_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        artifacts.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    monkeypatch.setattr(
        artifacts,
        "open_pinned_url",
        lambda *_args, **_kwargs: _Response(chunks=[MINIMAL_JPEG, b"too-large"]),
    )
    result = artifacts.download_direct_media_url(
        "https://pbs.twimg.com/media/a.jpg",
        assets_dir=tmp_path,
        index=1,
        allowed_hosts=frozenset({"pbs.twimg.com"}),
        max_bytes=len(MINIMAL_JPEG),
    )
    assert result is None
    assert not list(tmp_path.rglob("*.part"))


def _x_info(tweet_id="123", *, extractor="Twitter", webpage_url=None, media_id=None, display_id=None):
    return {
        "id": media_id or tweet_id,
        "display_id": tweet_id if display_id is None else display_id,
        "extractor": extractor,
        "extractor_key": extractor,
        "webpage_url": webpage_url or f"https://x.com/alice/status/{tweet_id}?s=20",
        "uploader": "alice",
        "timestamp": 123456,
    }


def test_x_yt_dlp_fallback_uses_hardened_commands_and_persists_whitelist(monkeypatch, tmp_path):
    commands: list[list[str]] = []
    final_info = _x_info(media_id="456")
    info_bytes = json.dumps(final_info).encode()

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] == 300
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "2026.07.01\n", "")
        if "--dump-single-json" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(_x_info(media_id="456")) + "\n", "")
        job_dir = Path(command[command.index("--paths") + 1])
        assert job_dir.stat().st_mode & 0o777 == 0o700
        (job_dir / "123.info.json").write_bytes(info_bytes)
        (job_dir / "123.mp4").write_bytes(MINIMAL_MP4)
        return subprocess.CompletedProcess(command, 0, "downloaded\n", "")

    monkeypatch.setattr(x_downloader, "_resolve_media_tool", lambda _name: "/usr/local/bin/yt-dlp")
    monkeypatch.setattr(x_downloader.subprocess, "run", run)

    result = x_downloader.run_x_yt_dlp_fallback(
        "https://x.com/alice/status/123",
        "123",
        assets_dir=tmp_path,
    )

    assert result.status == "downloaded"
    assert result.provenance == {
        "strategy": "yt_dlp",
        "tweet_id": "123",
        "extractor": "Twitter",
        "version": "2026.07.01",
        "media_id": "456",
        "uploader": "alice",
        "timestamp": 123456,
        "info_json_sha256": hashlib.sha256(info_bytes).hexdigest(),
    }
    assert result.files[0]["content_hash"] == hashlib.sha256(MINIMAL_MP4).hexdigest()
    assert result.files[0]["path"].stat().st_mode & 0o777 == 0o600
    inventory, download_command = commands[1], commands[2]
    for command in (inventory, download_command):
        assert "--ignore-config" in command
        assert "--no-playlist" in command
        assert "--socket-timeout" in command
        assert "--retries" in command
        assert "--fragment-retries" in command
        assert "--cookies-from-browser" not in command
    assert "--max-filesize" in download_command
    assert download_command[download_command.index("-o") + 1] == "%(id)s.%(ext)s"
    assert not list(tmp_path.glob(".x-yt-*"))
    assert not list(tmp_path.rglob("*.info.json"))


@pytest.mark.parametrize("mismatch", ["extractor", "display_id", "webpage"])
def test_x_yt_dlp_final_mismatch_writes_no_asset_and_cleans_staging(monkeypatch, tmp_path, mismatch):
    def run(command, **_kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "v1\n", "")
        if "--dump-single-json" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(_x_info()) + "\n", "")
        job_dir = Path(command[command.index("--paths") + 1])
        info = _x_info(
            extractor="Youtube" if mismatch == "extractor" else "Twitter",
            display_id="999" if mismatch == "display_id" else None,
            webpage_url="https://x.com/alice/status/999" if mismatch == "webpage" else None,
            media_id="456",
        )
        (job_dir / "result.info.json").write_text(json.dumps(info))
        (job_dir / "result.mp4").write_bytes(MINIMAL_MP4)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(x_downloader, "_resolve_media_tool", lambda _name: "yt-dlp")
    monkeypatch.setattr(x_downloader.subprocess, "run", run)
    result = x_downloader.run_x_yt_dlp_fallback(
        "https://x.com/alice/status/123", "123", assets_dir=tmp_path
    )
    assert result.status == "failed"
    assert result.files == []
    assert not list(tmp_path.rglob("*.mp4"))
    assert not list(tmp_path.glob(".x-yt-*"))


def test_x_yt_dlp_does_not_reuse_old_staging_and_redacts_diagnostics(monkeypatch, tmp_path):
    old = tmp_path / ".x-yt-old"
    old.mkdir()
    (old / "123.mp4").write_bytes(MINIMAL_MP4)

    def run(command, **_kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "v1\n", "")
        return subprocess.CompletedProcess(
            command,
            1,
            "https://x.com/a/status/123?token=secret",
            f"Cookie: secret Authorization=secret {Path.home()}/private",
        )

    monkeypatch.setattr(x_downloader, "_resolve_media_tool", lambda _name: "yt-dlp")
    monkeypatch.setattr(x_downloader.subprocess, "run", run)
    result = x_downloader.run_x_yt_dlp_fallback(
        "https://x.com/alice/status/123", "123", assets_dir=tmp_path
    )
    evidence = json.dumps(result.evidence)
    assert "token=secret" not in evidence
    assert "Cookie: secret" not in evidence
    assert "Authorization=secret" not in evidence
    assert str(Path.home()) not in evidence
    assert result.files == []
    assert (old / "123.mp4").exists()


def test_opencli_failure_then_fallback_persists_hash_and_provenance(monkeypatch, tmp_path):
    from backend.application.enrichment.media import download, x_session
    from contextlib import nullcontext

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card-x-fallback",
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://twitter.com/alice/status/123?x=1",
            "title": "post",
            "content": "text",
            "captured_at": "2026-07-14T00:00:00Z",
            "processed_at": "2026-07-14T00:00:00Z",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(x_session, "prepare_opencli_browser_session", lambda **_kwargs: {
        "status": "ready",
        "profile": {"alias": "infoos-fetch", "bound": True, "binding_status": "ready"},
    })
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: None)
    monkeypatch.setattr(x_session, "opencli_browser_lock", nullcontext)
    monkeypatch.setattr(download.subprocess, "run", lambda *_args, **_kwargs: type(
        "Result", (), {"returncode": 1, "stdout": "", "stderr": "unknown"}
    )())

    def fallback(url, tweet_id, *, assets_dir):
        assert url == "https://x.com/alice/status/123"
        path = assets_dir / "tweets" / "validated.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MINIMAL_MP4)
        return XFallbackResult(
            "downloaded",
            [{
                "path": path,
                "relative_path": "card-x-fallback/tweets/validated.mp4",
                "kind": "video",
                "mime": "video/mp4",
                "size": len(MINIMAL_MP4),
                "content_hash": hashlib.sha256(MINIMAL_MP4).hexdigest(),
            }],
            None,
            {
                "strategy": "yt_dlp", "tweet_id": tweet_id, "extractor": "Twitter",
                "version": "v1", "media_id": tweet_id, "uploader": "alice",
                "timestamp": 1, "info_json_sha256": "abc",
            },
            {"strategy": "yt_dlp", "version": "v1"},
        )

    monkeypatch.setattr(download, "run_x_yt_dlp_fallback", fallback)
    blocks = download.download_card_media("card-x-fallback", tmp_path)
    assert len(blocks) == 1
    asset = CardAssetRepository(tmp_path).list_for_card("card-x-fallback")[0]
    assert asset["content_hash"] == f"sha256:{hashlib.sha256(MINIMAL_MP4).hexdigest()}"
    assert asset["raw_payload"] == {
        "strategy": "yt_dlp", "tweet_id": "123", "extractor": "Twitter", "version": "v1",
        "media_id": "123", "uploader": "alice", "timestamp": 1, "info_json_sha256": "abc",
    }


def test_fallback_failure_creates_optional_gap_with_bounded_evidence(monkeypatch, tmp_path):
    from backend.application.enrichment.media import download

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card-x-gap", "source_platform": "x", "source_type": "social_post",
            "source_url": "https://x.com/alice/status/123", "title": "post", "content": "text",
            "captured_at": "2026-07-14T00:00:00Z", "processed_at": "2026-07-14T00:00:00Z",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(download, "run_x_media_download_lifecycle", lambda **_kwargs: type(
        "Lifecycle", (), {"command_result": None, "failure_reason": "x_media_download_failed", "details": {"download_attempt_count": 1}, "attempt_dir": None}
    )())
    monkeypatch.setattr(download, "run_x_yt_dlp_fallback", lambda *_args, **_kwargs: XFallbackResult(
        "failed", [], "x_yt_dlp_download_failed", {}, {"strategy": "yt_dlp", "stderr": "redacted"}
    ))
    assert download.download_card_media("card-x-gap", tmp_path) == []
    gap = CardContentBlockRepository(tmp_path).list_for_card("card-x-gap")[0]
    assert gap["raw_payload"]["reason"] == "x_media_download_fallback_failed"
    assert gap["raw_payload"]["details"]["opencli_reason"] == "x_media_download_failed"
