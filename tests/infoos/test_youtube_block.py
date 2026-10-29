"""Media enrichment tests — YouTube transcript / description / duration blocks."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.infrastructure.storage.repositories import (
    CardContentBlockRepository,
    CardRepository,
)


def test_youtube_metadata_is_a_separate_shared_block(monkeypatch, tmp_path):
    from backend.application.enrichment.youtube_blocks import enrich_youtube_card_blocks
    from backend.application.enrichment.transcripts.subtitles import SubtitleAttempt, _outcome
    from backend.domain.models.raw_item import RawItem, RawTimestamps

    monkeypatch.setattr(
        "backend.application.enrichment.youtube_blocks.fetch_youtube_subtitle",
        lambda *args, **kwargs: SubtitleAttempt(_outcome("unavailable", video_id="demoYt00005")),
    )
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_youtube_counts",
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://youtube.com/watch?v=demoYt00005",
            "title": "元数据测试",
            "content": "完整的视频说明",
            "captured_at": "2026-07-01T00:00:00Z",
            "processed_at": "2026-07-01T00:00:00Z",
            "raw_payload": {},
        }
    )
    blocks = enrich_youtube_card_blocks(
        "card_youtube_counts",
        RawItem(
            raw_item_id="demoYt00005",
            connector_id="youtube_meta",
            source_platform="youtube",
            source_type="video",
            source_url="https://youtube.com/watch?v=demoYt00005",
            captured_at="2026-07-01T00:00:00Z",
            raw_text="完整的视频说明",
            raw_timestamps=RawTimestamps(published_at="2026-07-01T00:00:00Z"),
            raw_metadata={"views": "1,234", "likes": 88, "duration": "12:34"},
        ),
        tmp_path,
    )

    metadata = next(block for block in blocks if block["block_kind"] == "metadata")
    assert metadata["raw_payload"] == {
        "published_at": "2026-07-01T00:00:00Z",
        "views": "1,234",
        "likes": 88,
        "duration": "12:34",
    }
    assert "观看次数: 1,234" in metadata["body"]


def test_youtube_metadata_rerun_reuses_existing_position(monkeypatch, tmp_path):
    from backend.application.enrichment.youtube_blocks import enrich_youtube_card_blocks
    from backend.application.enrichment.transcripts.subtitles import SubtitleAttempt, _outcome
    from backend.domain.models.raw_item import RawItem

    subtitle_calls = []

    def unavailable_subtitle(*args, **kwargs):
        subtitle_calls.append((args, kwargs))
        return SubtitleAttempt(_outcome("unavailable", video_id="stable-order"))

    monkeypatch.setattr(
        "backend.application.enrichment.youtube_blocks.fetch_youtube_subtitle",
        unavailable_subtitle,
    )
    card_id = "card_youtube_stable_metadata"
    source_url = "https://youtube.com/watch?v=stable-order"
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": card_id,
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": source_url,
            "title": "稳定顺序",
            "content": "视频说明",
            "captured_at": "2026-07-01T00:00:00Z",
            "processed_at": "2026-07-01T00:00:00Z",
            "raw_payload": {},
        }
    )

    first = enrich_youtube_card_blocks(
        card_id,
        RawItem(
            raw_item_id="stable-order",
            connector_id="youtube_meta",
            source_platform="youtube",
            source_type="video",
            source_url=source_url,
            captured_at="2026-07-01T00:00:00Z",
            raw_text="视频说明",
            raw_metadata={"views": 100},
        ),
        tmp_path,
    )
    first_metadata = next(block for block in first if block["block_kind"] == "metadata")

    second = enrich_youtube_card_blocks(
        card_id,
        RawItem(
            raw_item_id="stable-order",
            connector_id="youtube_meta",
            source_platform="youtube",
            source_type="video",
            source_url=source_url,
            captured_at="2026-07-01T00:00:00Z",
            raw_text="视频说明",
            raw_metadata={"views": 200},
        ),
        tmp_path,
    )
    second_metadata = next(block for block in second if block["block_kind"] == "metadata")

    assert second_metadata["position"] == first_metadata["position"]
    assert second_metadata["raw_payload"]["views"] == 200
    assert len(subtitle_calls) == 1
    persisted = CardContentBlockRepository(tmp_path).list_for_card(card_id)
    assert next(block for block in persisted if block["block_id"].endswith("_youtube_metadata"))[
        "position"
    ] == first_metadata["position"]


def test_youtube_transcript_falls_back_to_video_description(monkeypatch, tmp_path):
    from backend.application.enrichment.youtube_blocks import enrich_youtube_card_blocks
    from backend.application.enrichment.transcripts.subtitles import SubtitleAttempt, _outcome
    from backend.domain.models.raw_item import RawItem

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_youtube_1",
            "source_id": None,
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://www.youtube.com/watch?v=abc123",
            "source_author": "OpenAI",
            "title": "Demo video",
            "content": "Demo video",
            "summary": "",
            "captured_at": "2026-07-01T00:00:00Z",
            "processed_at": "2026-07-01T00:00:01Z",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.youtube_blocks.fetch_youtube_subtitle",
        lambda *args, **kwargs: SubtitleAttempt(_outcome("unavailable", video_id="abc123")),
    )

    blocks = enrich_youtube_card_blocks(
        "card_youtube_1",
        RawItem(
            raw_item_id="video_1",
            connector_id="youtube_openai",
            source_platform="youtube",
            source_type="video",
            source_url="https://www.youtube.com/watch?v=abc123",
            captured_at="2026-07-01T00:00:00Z",
            raw_text="Fallback description from the YouTube connector.",
            raw_metadata={"description": "Official video description with the useful copy."},
        ),
        tmp_path,
    )

    assert len(blocks) == 2
    assert blocks[0]["block_kind"] == "enrichment"
    assert blocks[0]["status"] == "enriched"
    assert blocks[0]["body"] == "Official video description with the useful copy."
    assert "description fallback" in blocks[0]["provenance"].lower()


def test_youtube_transcript_not_found_upgrades_from_opencli_metadata(monkeypatch, tmp_path):
    from backend.application.enrichment.youtube_blocks import enrich_youtube_card_blocks
    from backend.domain.models.raw_item import RawItem

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_youtube_meta",
            "source_id": None,
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://www.youtube.com/watch?v=meta123",
            "source_author": "Google DeepMind",
            "title": "",
            "content": "When millions of AI agents meet",
            "summary": "",
            "captured_at": "2026-07-01T00:00:00Z",
            "processed_at": "2026-07-01T00:00:01Z",
            "raw_payload": {},
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_card_youtube_meta_transcript",
            "card_id": "card_youtube_meta",
            "position": 1,
            "layer": "context",
            "block_kind": "transcript",
            "status": "not_found",
            "body": "",
            "segments": [],
            "source_url": "https://www.youtube.com/watch?v=meta123",
            "provenance": "YouTube transcript not available",
            "raw_payload": {},
        }
    )
    from backend.application.enrichment.transcripts.subtitles import SubtitleAttempt, _outcome
    monkeypatch.setattr(
        "backend.application.enrichment.youtube_blocks.fetch_youtube_subtitle",
        lambda *args, **kwargs: SubtitleAttempt(_outcome("unavailable", video_id="meta123")),
    )

    blocks = enrich_youtube_card_blocks(
        "card_youtube_meta",
        RawItem(
            raw_item_id="video_meta",
            connector_id="youtube_deepmind",
            source_platform="youtube",
            source_type="video",
            source_url="https://www.youtube.com/watch?v=meta123",
            captured_at="2026-07-01T00:00:00Z",
            raw_text="",
            raw_metadata={
                "raw_payload": {
                    "opencli_record": {
                        "text": "When millions of AI agents meet",
                        "title": "When millions of AI agents meet",
                    }
                }
            },
        ),
        tmp_path,
    )

    stored = CardContentBlockRepository(tmp_path).list_for_card("card_youtube_meta")
    assert len(blocks) == 1
    assert all(block["block_kind"] == "enrichment" for block in stored)
    assert not any(block["body"] == "When millions of AI agents meet" for block in stored)


def test_task_content_enrichment_handles_youtube_metadata_fallback(monkeypatch, tmp_path):
    from backend.application.enrichment.task_blocks import enrich_card_blocks_for_task

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_youtube_async",
            "source_id": None,
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://www.youtube.com/watch?v=async123",
            "source_author": "OpenAI",
            "title": "",
            "content": "Advent Accelerates Deals with ChatGPT + Codex",
            "summary": "Advent Accelerates Deals with ChatGPT + Codex",
            "captured_at": "2026-07-01T00:00:00Z",
            "processed_at": "2026-07-01T00:00:01Z",
            "raw_payload": {
                "summary": "Advent Accelerates Deals with ChatGPT + Codex",
                "raw_payload": {
                    "opencli_record": {
                        "text": "Advent Accelerates Deals with ChatGPT + Codex",
                    }
                },
            },
        }
    )
    from backend.application.enrichment.transcripts.subtitles import SubtitleAttempt, _outcome
    monkeypatch.setattr(
        "backend.application.enrichment.youtube_blocks.fetch_youtube_subtitle",
        lambda *args, **kwargs: SubtitleAttempt(_outcome("unavailable", video_id="async123")),
    )

    assert enrich_card_blocks_for_task("card_youtube_async", tmp_path) == 1
    block = CardContentBlockRepository(tmp_path).list_for_card("card_youtube_async")[0]
    assert block["block_kind"] == "enrichment"
    assert block["raw_payload"]["subtitle_attempt_status"] == "unavailable"
    assert block["body"] == ""


def test_description_enrichment_is_not_reused_as_native_transcript(tmp_path):
    from backend.application.enrichment.transcripts.native import write_native_transcript_block_if_available

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_description_only",
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://youtube.com/watch?v=abc123",
            "title": "video",
            "content": "video",
            "captured_at": "2026-07-14T00:00:00Z",
            "processed_at": "2026-07-14T00:00:00Z",
            "raw_payload": {},
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_card_description_only_youtube_description_fallback",
            "card_id": "card_description_only",
            "position": 0,
            "layer": "context",
            "block_kind": "enrichment",
            "status": "enriched",
            "body": "real description, not a transcript",
            "source_url": "https://youtube.com/watch?v=abc123",
            "provenance": "YouTube description fallback",
            "raw_payload": {"strategy": "youtube_description_fallback", "usable": False},
        }
    )
    assert write_native_transcript_block_if_available(
        tmp_path, {"card_id": "card_description_only", "asset_id": "asset-video"}
    ) is None


def test_content_enrich_persists_terminal_blocked_subtitle_outcome(monkeypatch, tmp_path):
    from backend.application.enrichment.transcripts.subtitles import SubtitleAttempt, _outcome
    from backend.application.tasks.handlers.acquisition import handle_content_enrich
    from backend.application.tasks.types import Task, TaskType

    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_youtube_blocked_subtitle",
            "source_platform": "youtube",
            "source_type": "video",
            "source_url": "https://youtube.com/watch?v=blocked123",
            "title": "video",
            "content": "video",
            "captured_at": "2026-07-14T00:00:00Z",
            "processed_at": "2026-07-14T00:00:00Z",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.youtube_blocks.fetch_youtube_subtitle",
        lambda *_args, **_kwargs: SubtitleAttempt(
            _outcome("blocked", video_id="blocked123", evidence={"reason": "yt_dlp_missing"})
        ),
    )
    result = handle_content_enrich(
        Task(
            task_id="enrich-blocked-subtitle",
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id="card_youtube_blocked_subtitle",
        ),
        tmp_path,
    )
    assert result["status"] == "success"
    assert result["artifacts"]["subtitle_outcome"]["subtitle_attempt_status"] == "blocked"
    block = CardContentBlockRepository(tmp_path).list_for_card("card_youtube_blocked_subtitle")[0]
    assert block["raw_payload"]["subtitle_policy_version"]


# ── Image understanding tests ──────────────────────────────────────────

from tests.infoos._factories import setup_card_and_asset  # noqa: F401

def test_extract_audio_returns_error_when_ffmpeg_missing(monkeypatch, tmp_path):
    """Returns clear error when ffmpeg is not installed."""
    from backend.application.enrichment.media.video_transcription import extract_audio

    # Simulate ffmpeg not available
    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription._ffmpeg_available",
        lambda: False,
    )

    video = tmp_path / "test.mp4"
    video.write_bytes(b"\x00" * 100)

    result = extract_audio(str(video), output_dir=str(tmp_path / "audio"))
    assert result["audio_path"] is None
    assert result["error"] is not None
    assert "ffmpeg" in result["error"].lower()

def test_extract_audio_with_ffmpeg(monkeypatch, tmp_path):
    """Extracts audio from video using mocked ffmpeg."""
    from backend.application.enrichment.media.video_transcription import extract_audio

    # Mock ffmpeg availability
    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription._ffmpeg_available",
        lambda: True,
    )

    fake_stderr = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'test.mp4':\n"
        "  Duration: 00:00:30.50, start: 0.000000, bitrate: 500 kb/s\n"
        "Output: audio.wav\n"
    )

    def fake_run(cmd, **kwargs):
        out_dir = tmp_path / "audio"
        out_dir.mkdir(parents=True, exist_ok=True)
        audio = out_dir / "test.wav"
        audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
        return type("FakeResult", (), {"returncode": 0, "stderr": fake_stderr})()

    monkeypatch.setattr("subprocess.run", fake_run)

    video = tmp_path / "test.mp4"
    video.write_bytes(b"\x00" * 100)

    result = extract_audio(str(video), output_dir=str(tmp_path / "audio"))
    assert result["audio_path"] is not None
    assert result["format"] == "wav"
    assert result["duration_seconds"] == 30.0
    assert result["error"] is None


def test_extract_audio_uses_unique_output_path_for_same_video_filename(monkeypatch, tmp_path):
    """Avoid collisions when different cards both contain repost_1.mp4."""
    from backend.application.enrichment.media.video_transcription import extract_audio

    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription._ffmpeg_available",
        lambda: True,
    )
    written_paths: list[Path] = []

    def fake_run(cmd, **kwargs):
        audio_path = Path(cmd[-2])
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
        written_paths.append(audio_path)
        fake_stderr = "Duration: 00:00:01.00, start: 0.000000, bitrate: 500 kb/s\n"
        return type("FakeResult", (), {"returncode": 0, "stderr": fake_stderr})()

    monkeypatch.setattr("subprocess.run", fake_run)
    first = tmp_path / "card_a" / "tweets" / "repost_1.mp4"
    second = tmp_path / "card_b" / "tweets" / "repost_1.mp4"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    first_result = extract_audio(str(first), output_dir=str(tmp_path / "audio"))
    second_result = extract_audio(str(second), output_dir=str(tmp_path / "audio"))

    assert first_result["audio_path"] != second_result["audio_path"]
    assert len(set(written_paths)) == 2


def test_extract_audio_handles_missing_file(tmp_path):
    """Returns error when video file does not exist."""
    from backend.application.enrichment.media.video_transcription import extract_audio

    result = extract_audio(str(tmp_path / "nonexistent.mp4"), output_dir=str(tmp_path / "audio"))
    assert result["error"] is not None
    assert "not found" in result["error"].lower()

def test_extract_audio_reports_no_audio_stream(monkeypatch, tmp_path):
    from backend.application.enrichment.media.video_transcription import extract_audio

    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription._ffmpeg_available",
        lambda: True,
    )

    def fake_run(cmd, **kwargs):
        return type(
            "FakeResult",
            (),
            {
                "returncode": 1,
                "stderr": "Output file #0 does not contain any stream",
            },
        )()

    monkeypatch.setattr("subprocess.run", fake_run)

    video = tmp_path / "silent.mp4"
    video.write_bytes(b"\x00" * 100)

    result = extract_audio(str(video), output_dir=str(tmp_path / "audio"))
    assert result["audio_path"] is None
    assert result["error"] == "Video has no audio stream"

def test_transcribe_audio_returns_error_when_no_backend(monkeypatch, tmp_path):
    """Returns a blocked result when local whisper is unavailable."""
    from backend.application.enrichment.media.video_transcription import transcribe_audio

    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription._whisper_available",
        lambda: False,
    )

    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    result = transcribe_audio(str(audio))
    assert result["status"] == "blocked"
    assert result["error"] == "blocked_asr_provider_missing"

def test_transcribe_audio_via_whisper(monkeypatch, tmp_path):
    """Transcribes audio using mocked whisper package."""
    from backend.application.enrichment.media.video_transcription import transcribe_audio

    # Mock the whisper availability check and the whisper transcription function
    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription._whisper_available",
        lambda: True,
    )

    def fake_transcribe_with_whisper(audio_path, *, language=None):
        return {
            "text": "Hello, this is a test transcription.",
            "language": "en",
            "model": "whisper-base",
            "error": None,
        }

    monkeypatch.setattr(
        "backend.application.enrichment.media.video_transcription._transcribe_with_whisper",
        fake_transcribe_with_whisper,
    )

    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    result = transcribe_audio(str(audio))
    assert "Hello" in result["text"]
    assert result["language"] == "en"
    assert result["error"] is None


# ── Media pipeline tests ───────────────────────────────────────────────

# ── Re-summary tests ───────────────────────────────────────────────────

# ── Helpers ────────────────────────────────────────────────────────────



def test_youtube_task_reads_source_metadata_with_legacy_payload_fallback(monkeypatch, tmp_path):
    from backend.application.enrichment.task_blocks import enrich_card_blocks_for_task
    from backend.application.enrichment.transcripts.subtitles import SubtitleAttempt, _outcome
    from backend.application.pipeline.information_card_builder import build_information_card, information_card_payload
    from backend.application.pipeline.normalize import normalize_raw_item
    from backend.domain.models.raw_item import RawItem
    from backend.infrastructure.storage.repositories import RawItemRepository

    monkeypatch.setattr("backend.application.enrichment.youtube_blocks.fetch_youtube_subtitle",
                        lambda *a, **kw: SubtitleAttempt(_outcome("unavailable", video_id="source")))
    for legacy, with_raw in ((True, True), (False, True), (True, False), (False, False)):
        suffix = f"{legacy}-{with_raw}"
        raw = RawItem(raw_item_id=suffix, connector_id="youtube", source_platform="youtube",
                      source_type="video", source_url=f"https://youtube.com/watch?v={suffix}",
                      captured_at="2026-10-19T00:00:00Z", raw_text="Video title",
                      raw_metadata={"description": "Full description", "views": 123,
                                    "opencli_record": {"likes": 8, "duration": "12:34"}})
        draft = build_information_card(normalize_raw_item(raw), raw)
        payload = information_card_payload(draft)
        if legacy:
            payload["raw_payload"] = draft.model_dump(mode="json")
        CardRepository(tmp_path).upsert_card(payload)
        if with_raw:
            RawItemRepository(tmp_path).upsert_raw_items([raw])
        enrich_card_blocks_for_task(draft.card_id, tmp_path)
        blocks = CardContentBlockRepository(tmp_path).list_for_card(draft.card_id)
        if legacy or with_raw:
            assert next(b for b in blocks if b["block_id"].endswith("_youtube_description_fallback"))["body"] == "Full description"
            assert next(b for b in blocks if b["block_kind"] == "metadata")["raw_payload"] == {
                "views": 123, "likes": 8, "duration": "12:34"}
        else:
            # Lost raw input cannot supply description/duration; durable counts and terminal outcome remain usable.
            assert next(b for b in blocks if b["block_kind"] == "metadata")["raw_payload"] == {"views": 123, "likes": 8}
            assert any(b["raw_payload"].get("subtitle_attempt_status") == "unavailable" for b in blocks)
