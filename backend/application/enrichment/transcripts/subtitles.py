from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from backend.application.enrichment.media.tooling import _resolve_media_tool

SUBTITLE_POLICY_VERSION = "youtube-subtitle-v1"
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
_TIMING = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})"
)


@dataclass(frozen=True)
class YouTubeURL:
    video_id: str
    canonical_url: str


@dataclass(frozen=True)
class SubtitleSelection:
    language: str
    source: str


@dataclass(frozen=True)
class SubtitleAttempt:
    outcome: dict[str, Any]
    text: str = ""
    segments: tuple[dict[str, Any], ...] = ()
    description: str = ""


def parse_youtube_url(value: str) -> YouTubeURL | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in _YOUTUBE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    video_id = ""
    if host == "youtu.be":
        parts = [part for part in parsed.path.split("/") if part]
        video_id = parts[0] if len(parts) == 1 else ""
    elif parsed.path == "/watch":
        values = parse_qs(parsed.query).get("v", [])
        video_id = values[0] if len(values) == 1 else ""
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 2 and parts[0] in {"shorts", "live"}:
            video_id = parts[1]
    if not _VIDEO_ID.fullmatch(video_id):
        return None
    return YouTubeURL(video_id, f"https://www.youtube.com/watch?v={video_id}")


def select_subtitle(
    subtitles: dict[str, Any],
    automatic_captions: dict[str, Any],
) -> SubtitleSelection | None:
    languages = set(subtitles) | set(automatic_captions)
    ordered = sorted(languages, key=_language_rank)
    for language in ordered:
        if _language_rank(language)[0] >= 4:
            continue
        if language in subtitles:
            return SubtitleSelection(language, "manual")
        if language in automatic_captions:
            return SubtitleSelection(language, "auto")
    return None


def parse_timed_segments(content: str) -> list[dict[str, Any]]:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        timing = _TIMING.search(lines[index])
        if timing is None:
            index += 1
            continue
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            cleaned = _clean_caption_text(lines[index])
            if cleaned:
                text_lines.append(cleaned)
            index += 1
        text = " ".join(text_lines).strip()
        if text:
            segments.append(
                {
                    "start": _timestamp_seconds(timing.group("start")),
                    "end": _timestamp_seconds(timing.group("end")),
                    "text": text,
                }
            )
        index += 1
    return segments


def fetch_youtube_subtitle(url: str, *, storage_dir: Path) -> SubtitleAttempt:
    parsed = parse_youtube_url(url)
    if parsed is None:
        return SubtitleAttempt(_outcome("failed", video_id=None, evidence={"reason": "invalid_youtube_url"}))
    executable = _resolve_media_tool("yt-dlp")
    if executable is None:
        return SubtitleAttempt(_outcome("blocked", video_id=parsed.video_id, evidence={"reason": "yt_dlp_missing"}))
    storage_dir.mkdir(parents=True, exist_ok=True)
    job_dir = Path(tempfile.mkdtemp(prefix=".subtitle-", dir=storage_dir))
    job_dir.chmod(0o700)
    try:
        inventory = _run(
            [
                executable, "--ignore-config", "--no-playlist", "--skip-download", "--dump-single-json",
                "--quiet", "--no-warnings", "--socket-timeout", "15", "--retries", "2",
                "--fragment-retries", "2", parsed.canonical_url,
            ]
        )
        if inventory is None:
            return SubtitleAttempt(_outcome("failed", video_id=parsed.video_id, evidence={"reason": "inventory_timeout"}))
        if inventory.returncode != 0:
            return SubtitleAttempt(
                _outcome(
                    "failed",
                    video_id=parsed.video_id,
                    evidence={"reason": "inventory_failed", "exit_code": inventory.returncode, "stderr": redact_youtube_diagnostic(inventory.stderr)},
                )
            )
        info_bytes = inventory.stdout.encode()
        info = _json_dict(inventory.stdout)
        if not _valid_youtube_info(info, parsed.video_id):
            return SubtitleAttempt(_outcome("failed", video_id=parsed.video_id, evidence={"reason": "inventory_mismatch"}))
        description = _real_description(info)
        manual_value = info.get("subtitles")
        automatic_value = info.get("automatic_captions")
        manual: dict[str, Any] = manual_value if isinstance(manual_value, dict) else {}
        automatic: dict[str, Any] = automatic_value if isinstance(automatic_value, dict) else {}
        selection = select_subtitle(manual, automatic)
        if selection is None:
            return SubtitleAttempt(
                _outcome(
                    "unavailable",
                    video_id=parsed.video_id,
                    fallback="youtube_description_fallback" if description else "none",
                    evidence={"info_json_sha256": hashlib.sha256(info_bytes).hexdigest()},
                ),
                description=description,
            )
        source_flags = ["--write-subs", "--no-write-auto-subs"] if selection.source == "manual" else ["--write-auto-subs", "--no-write-subs"]
        downloaded = _run(
            [
                executable, "--ignore-config", "--no-playlist", "--skip-download", *source_flags,
                "--sub-langs", selection.language, "--sub-format", "vtt/srt/best", "--write-info-json",
                "--socket-timeout", "15", "--retries", "2", "--fragment-retries", "2",
                "--paths", str(job_dir), "-o", "%(id)s.%(ext)s", parsed.canonical_url,
            ]
        )
        _chmod_files(job_dir)
        if downloaded is None or downloaded.returncode != 0:
            return SubtitleAttempt(
                _outcome(
                    "failed",
                    video_id=parsed.video_id,
                    language=selection.language,
                    source=selection.source,
                    evidence={"reason": "subtitle_download_failed", "stderr": redact_youtube_diagnostic(downloaded.stderr if downloaded else "timeout")},
                ),
                description=description,
            )
        info_files = list(job_dir.glob("*.info.json"))
        sidecars = [path for path in job_dir.iterdir() if path.suffix.lower() in {".vtt", ".srt"}]
        if len(info_files) != 1 or len(sidecars) != 1:
            return SubtitleAttempt(_outcome("failed", video_id=parsed.video_id, language=selection.language, source=selection.source, evidence={"reason": "sidecar_ambiguous"}), description=description)
        final_bytes = info_files[0].read_bytes()
        info_files[0].unlink(missing_ok=True)
        try:
            final_info = json.loads(final_bytes)
        except json.JSONDecodeError:
            final_info = None
        if not _valid_youtube_info(final_info, parsed.video_id) or not _selection_present(final_info, selection):
            return SubtitleAttempt(_outcome("failed", video_id=parsed.video_id, language=selection.language, source=selection.source, evidence={"reason": "sidecar_ownership_mismatch"}), description=description)
        sidecar = sidecars[0]
        sidecar_bytes = sidecar.read_bytes()
        try:
            segments = parse_timed_segments(sidecar_bytes.decode("utf-8-sig"))
        except UnicodeDecodeError:
            segments = []
        if not segments:
            return SubtitleAttempt(_outcome("unavailable", video_id=parsed.video_id, language=selection.language, source=selection.source, evidence={"reason": "sidecar_unusable"}), description=description)
        persisted = storage_dir / f"{parsed.video_id}.{selection.language}.{selection.source}{sidecar.suffix.lower()}"
        shutil.copyfile(sidecar, persisted)
        persisted.chmod(0o600)
        sidecar_hash = hashlib.sha256(sidecar_bytes).hexdigest()
        outcome = _outcome(
            "usable",
            video_id=parsed.video_id,
            language=selection.language,
            source=selection.source,
            strategy="yt_dlp_subtitle",
            sidecar_reference=persisted.name,
            sidecar_hash=sidecar_hash,
            evidence={"info_json_sha256": hashlib.sha256(final_bytes).hexdigest()},
        )
        return SubtitleAttempt(outcome, " ".join(segment["text"] for segment in segments), tuple(segments), description)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _outcome(
    status: str,
    *,
    video_id: str | None,
    fallback: str = "none",
    language: str | None = None,
    source: str | None = None,
    strategy: str = "yt_dlp_inventory",
    sidecar_reference: str | None = None,
    sidecar_hash: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "subtitle_attempt_status": status,
        "subtitle_policy_version": SUBTITLE_POLICY_VERSION,
        "usable_transcript": status == "usable",
        "fallback": fallback,
        "transcript_fallback": fallback,
        "strategy": strategy,
        "language": language,
        "manual_or_auto": source,
        "video_id": video_id,
        "sidecar_reference": sidecar_reference,
        "sidecar_sha256": sidecar_hash,
        "evidence": evidence or {},
    }


def _language_rank(language: str) -> tuple[int, str]:
    lowered = language.lower()
    if lowered == "zh-hans":
        return (0, lowered)
    if lowered == "zh-cn":
        return (1, lowered)
    if lowered.startswith("zh"):
        return (2, lowered)
    if lowered.startswith("en"):
        return (3, lowered)
    return (4, lowered)


def _clean_caption_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", value)
    return " ".join(html.unescape(without_tags).split())


def _timestamp_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _run(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=300, check=False, preexec_fn=lambda: os.umask(0o077))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _json_dict(value: str) -> dict[str, Any] | None:
    for line in reversed(value.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _valid_youtube_info(info: Any, video_id: str) -> bool:
    if not isinstance(info, dict) or str(info.get("id") or "") != video_id:
        return False
    extractors = [str(info.get(key) or "").lower() for key in ("extractor", "extractor_key")]
    if not any(value.startswith("youtube") for value in extractors):
        return False
    if any(value and not value.startswith("youtube") for value in extractors):
        return False
    webpage = parse_youtube_url(str(info.get("webpage_url") or ""))
    return webpage is not None and webpage.video_id == video_id


def _selection_present(info: Any, selection: SubtitleSelection) -> bool:
    if not isinstance(info, dict):
        return False
    key = "subtitles" if selection.source == "manual" else "automatic_captions"
    inventory = info.get(key)
    return isinstance(inventory, dict) and selection.language in inventory


def _real_description(info: dict[str, Any]) -> str:
    value = info.get("description")
    return " ".join(str(value or "").split())


def _chmod_files(job_dir: Path) -> None:
    for path in job_dir.rglob("*"):
        if path.is_file():
            path.chmod(0o600)


def redact_youtube_diagnostic(value: str) -> str:
    text = re.sub(r"https?://[^\s]+", lambda match: _strip_query(match.group(0)), value, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(authorization|cookie|set-cookie|x-api-key)\s*[:=]\s*[^\r\n]*", r"\1=[REDACTED]", text)
    return text.replace(str(Path.home()), "~")[:800]


def _strip_query(value: str) -> str:
    try:
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
    except ValueError:
        return "[REDACTED_URL]"
