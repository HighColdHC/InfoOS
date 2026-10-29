from __future__ import annotations

import hashlib
import subprocess
from functools import lru_cache
from pathlib import Path


def _ffmpeg_available() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _whisper_available() -> bool:
    try:
        import whisper  # noqa: F401
        return True
    except ImportError:
        return False


def extract_audio(video_path: str, *, output_dir: str) -> dict:
    """
    Extract audio from video using ffmpeg.

    Returns:
    {
        "audio_path": str | None,
        "format": str,        # "wav" or "mp3"
        "duration_seconds": float,
        "error": str | None
    }
    """
    try:
        video = Path(video_path)
        if not video.exists():
            return {
                "audio_path": None,
                "format": "wav",
                "duration_seconds": 0.0,
                "error": f"Video file not found: {video_path}",
            }

        if not _ffmpeg_available():
            return {
                "audio_path": None,
                "format": "wav",
                "duration_seconds": 0.0,
                "error": "ffmpeg not installed. Install ffmpeg for video transcription support.",
            }

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        basename = video.stem
        path_hash = hashlib.sha256(str(video.resolve()).encode("utf-8")).hexdigest()[:12]
        audio_path = out_dir / f"{basename}_{path_hash}.wav"

        result = subprocess.run(
            [
                "ffmpeg",
                "-i", str(video),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                str(audio_path),
                "-y",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            if _looks_like_missing_audio_stream(stderr):
                return {
                    "audio_path": None,
                    "format": "wav",
                    "duration_seconds": 0.0,
                    "error": "Video has no audio stream",
                }
            return {
                "audio_path": None,
                "format": "wav",
                "duration_seconds": 0.0,
                "error": f"ffmpeg audio extraction failed: {stderr[:500]}",
            }

        # Parse duration from ffmpeg stderr
        duration = _parse_ffmpeg_duration(result.stderr)

        return {
            "audio_path": str(audio_path),
            "format": "wav",
            "duration_seconds": duration,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {
            "audio_path": None,
            "format": "wav",
            "duration_seconds": 0.0,
            "error": "ffmpeg audio extraction timed out (>300s)",
        }
    except (OSError, ValueError) as exc:
        return {
            "audio_path": None,
            "format": "wav",
            "duration_seconds": 0.0,
            "error": str(exc),
        }


def _parse_ffmpeg_duration(stderr: str) -> float:
    """Parse duration from ffmpeg stderr. Format: Duration: HH:MM:SS.ms"""
    import re

    match = re.search(r"Duration:\s*(\d{2}):(\d{2}):(\d{2})\.(\d+)", stderr)
    if not match:
        return 0.0
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    return hours * 3600.0 + minutes * 60.0 + seconds


def _looks_like_missing_audio_stream(stderr: str) -> bool:
    normalized = stderr.lower()
    return (
        "does not contain any stream" in normalized
        or "matches no streams" in normalized
        or "output file #0 does not contain any stream" in normalized
    )


def transcribe_audio(
    audio_path: str,
    *,
    language: str | None = None,
) -> dict:
    """
    Transcribe audio to text.

    Returns:
    {
        "text": str,
        "language": str,
        "model": str,
        "error": str | None
    }
    """
    try:
        audio = Path(audio_path)
        if not audio.exists():
            return {
                "text": "",
                "language": "",
                "model": "",
                "error": f"Audio file not found: {audio_path}",
            }

        # Use the local whisper Python package when installed.
        if _whisper_available():
            return _transcribe_with_whisper(audio_path, language=language)

        return {
            "status": "blocked",
            "text": "",
            "language": "",
            "model": "",
            "error": "blocked_asr_provider_missing",
        }

    except Exception as exc:  # noqa: BLE001 - Whisper model and decoder failures must return an explicit transcription error to the caller
        return {
            "text": "",
            "language": "",
            "model": "",
            "error": str(exc),
        }


def _transcribe_with_whisper(audio_path: str, *, language: str | None = None) -> dict:
    model = _load_whisper_model("base")
    options = {}
    if language:
        options["language"] = language
    result = model.transcribe(audio_path, **options)
    return {
        "text": result.get("text", "").strip(),
        "language": result.get("language", language or "unknown"),
        "model": "whisper-base",
        "error": None,
    }


@lru_cache(maxsize=2)
def _load_whisper_model(model_name: str):
    import whisper

    return whisper.load_model(model_name)
