from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from backend.application.enrichment.transcripts import subtitles


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=abc123",
        "https://user@youtube.com/watch?v=abc123",
        "https://youtube.com:444/watch?v=abc123",
        "https://youtube.com.evil.example/watch?v=abc123",
        "https://evilyoutube.com/watch?v=abc123",
    ],
)
def test_youtube_url_rejects_unsafe_hosts_and_authority(url):
    assert subtitles.parse_youtube_url(url) is None


def test_youtube_url_normalizes_supported_forms():
    assert subtitles.parse_youtube_url("https://youtu.be/abc123?t=1").canonical_url == (
        "https://www.youtube.com/watch?v=abc123"
    )
    assert subtitles.parse_youtube_url("https://m.youtube.com/shorts/abc123").video_id == "abc123"


def test_subtitle_selection_language_and_manual_priority():
    manual = {"en": [{}], "zh-CN": [{}], "zh-TW": [{}]}
    automatic = {"zh-Hans": [{}], "zh-CN": [{}], "en-US": [{}]}
    selected = subtitles.select_subtitle(manual, automatic)
    assert selected == subtitles.SubtitleSelection("zh-Hans", "auto")
    assert subtitles.select_subtitle({"zh-CN": [{}]}, {"zh-CN": [{}]}) == subtitles.SubtitleSelection(
        "zh-CN", "manual"
    )
    assert subtitles.select_subtitle({"en": [{}]}, {"zh-TW": [{}]}) == subtitles.SubtitleSelection(
        "zh-TW", "auto"
    )


@pytest.mark.parametrize(
    "content",
    [
        "",
        "WEBVTT\n\n00:00:00.000 --> broken\ntext",
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<c></c>",
        "1\n00:00:00,000 --> 00:00:01,000\n<i></i>\n",
    ],
)
def test_empty_corrupt_or_markup_only_sidecar_is_unusable(content):
    assert subtitles.parse_timed_segments(content) == []


def _info(*, video_id="abc123", extractor="Youtube", description="real description"):
    return {
        "id": video_id,
        "extractor": extractor,
        "extractor_key": extractor,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "description": description,
        "subtitles": {"zh-CN": [{"ext": "vtt"}], "en": [{"ext": "vtt"}]},
        "automatic_captions": {"zh-Hans": [{"ext": "vtt"}]},
    }


def test_two_stage_subtitle_downloads_only_selected_source_and_persists_segments(monkeypatch, tmp_path):
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        if "--dump-single-json" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(_info()) + "\n", "")
        job = Path(command[command.index("--paths") + 1])
        (job / "abc123.info.json").write_text(json.dumps(_info()))
        (job / "abc123.zh-Hans.vtt").write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n<c>Hello</c> world\n"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subtitles, "_resolve_media_tool", lambda _name: "yt-dlp")
    monkeypatch.setattr(subtitles.subprocess, "run", run)
    storage = tmp_path / "subtitles"
    stale = storage / "stale.en.vtt"
    storage.mkdir()
    stale.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nstale\n")

    result = subtitles.fetch_youtube_subtitle(
        "https://youtube.com/watch?v=abc123", storage_dir=storage
    )

    assert result.outcome["subtitle_attempt_status"] == "usable"
    assert result.outcome["language"] == "zh-Hans"
    assert result.outcome["manual_or_auto"] == "auto"
    assert result.segments == ({"start": 0.0, "end": 1.5, "text": "Hello world"},)
    selected_command = commands[1]
    assert selected_command[selected_command.index("--sub-langs") + 1] == "zh-Hans"
    assert "--write-auto-subs" in selected_command
    assert "--no-write-subs" in selected_command
    assert stale.exists()
    assert not list(storage.glob(".subtitle-*"))


@pytest.mark.parametrize("field", ["extractor", "id"])
def test_subtitle_inventory_mismatch_writes_no_sidecar(monkeypatch, tmp_path, field):
    info = _info(extractor="Twitter" if field == "extractor" else "Youtube")
    if field == "id":
        info["id"] = "wrong"
    monkeypatch.setattr(subtitles, "_resolve_media_tool", lambda _name: "yt-dlp")
    monkeypatch.setattr(
        subtitles.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(info) + "\n", ""),
    )
    result = subtitles.fetch_youtube_subtitle(
        "https://youtube.com/watch?v=abc123", storage_dir=tmp_path
    )
    assert result.outcome["subtitle_attempt_status"] == "failed"
    assert not list(tmp_path.rglob("*.vtt"))
