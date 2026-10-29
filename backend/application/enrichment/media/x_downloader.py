from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from backend.application.enrichment.media.artifacts import looks_like_media_file
from backend.application.enrichment.media.tooling import _resolve_media_tool
from backend.application.enrichment.media.x_urls import parse_canonical_x_status_url

_MAX_MEDIA_BYTES = 200 * 1024 * 1024
_COMMAND_TIMEOUT_SECONDS = 300
_DIAGNOSTIC_LIMIT = 800
_MEDIA_EXTENSIONS = {
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".webp": ("image", "image/webp"),
    ".mp4": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".webm": ("video", "video/webm"),
}


@dataclass(frozen=True)
class XFallbackResult:
    status: str
    files: list[dict[str, Any]]
    reason: str | None
    provenance: dict[str, Any]
    evidence: dict[str, Any]


def run_x_yt_dlp_fallback(
    canonical_url: str,
    tweet_id: str,
    *,
    assets_dir: Path,
) -> XFallbackResult:
    executable = _resolve_media_tool("yt-dlp")
    if executable is None:
        return _failure("yt_dlp_missing", version=None)
    assets_dir.mkdir(parents=True, exist_ok=True)
    job_dir = Path(tempfile.mkdtemp(prefix=".x-yt-", dir=assets_dir))
    job_dir.chmod(0o700)
    version = _tool_version(executable)
    try:
        inventory_command = [
            executable,
            "--ignore-config", "--no-playlist", "--skip-download", "--dump-single-json",
            "--quiet", "--no-warnings", "--socket-timeout", "15", "--retries", "2",
            "--fragment-retries", "2", canonical_url,
        ]
        inventory = _run_command(inventory_command)
        if inventory is None:
            return _failure("x_yt_dlp_inventory_timeout", version=version)
        if inventory.returncode != 0:
            return _failure(
                "x_yt_dlp_inventory_failed",
                version=version,
                stdout=inventory.stdout,
                stderr=inventory.stderr,
                exit_code=inventory.returncode,
            )
        inventory_info = _parse_json_output(inventory.stdout)
        if not _valid_x_inventory_info(inventory_info, tweet_id):
            return _failure("x_yt_dlp_inventory_mismatch", version=version)

        download_command = [
            executable,
            "--ignore-config", "--no-playlist", "--write-info-json", "--socket-timeout", "15",
            "--retries", "2", "--fragment-retries", "2", "--max-filesize", str(_MAX_MEDIA_BYTES),
            "--paths", str(job_dir), "-o", "%(id)s.%(ext)s", canonical_url,
        ]
        downloaded = _run_command(download_command)
        _secure_job_files(job_dir)
        if downloaded is None:
            return _failure("x_yt_dlp_download_timeout", version=version)
        if downloaded.returncode != 0:
            return _failure(
                "x_yt_dlp_download_failed",
                version=version,
                stdout=downloaded.stdout,
                stderr=downloaded.stderr,
                exit_code=downloaded.returncode,
            )
        info_files = list(job_dir.glob("*.info.json"))
        if len(info_files) != 1:
            return _failure("x_yt_dlp_info_missing", version=version)
        info_path = info_files[0]
        info_bytes = info_path.read_bytes()
        info_sha256 = hashlib.sha256(info_bytes).hexdigest()
        try:
            final_info = json.loads(info_bytes)
        except json.JSONDecodeError:
            return _failure("x_yt_dlp_info_invalid", version=version)
        finally:
            info_path.unlink(missing_ok=True)
        if not isinstance(final_info, dict) or not _valid_x_final_info(final_info, tweet_id):
            return _failure("x_yt_dlp_final_info_mismatch", version=version)

        provenance = _provenance(final_info, tweet_id, version, info_sha256)
        files = _persist_validated_media(job_dir, assets_dir, tweet_id)
        if not files:
            return _failure("x_yt_dlp_no_media_files", version=version)
        return XFallbackResult(
            status="downloaded",
            files=files,
            reason=None,
            provenance=provenance,
            evidence={"strategy": "yt_dlp", "version": version, "exit_code": downloaded.returncode},
        )
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
            preexec_fn=_restrict_child_umask,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _restrict_child_umask() -> None:
    os.umask(0o077)


def _tool_version(executable: str) -> str | None:
    result = _run_command([executable, "--version"])
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip()[:80] or None


def _parse_json_output(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _valid_x_inventory_info(info: dict[str, Any] | None, tweet_id: str) -> bool:
    if not _valid_x_extractor_and_webpage(info, tweet_id):
        return False
    display_id = str(info.get("display_id") or "")
    tweet_level_id = display_id or str(info.get("id") or "")
    return tweet_level_id == tweet_id


def _valid_x_final_info(info: dict[str, Any] | None, tweet_id: str) -> bool:
    if not _valid_x_extractor_and_webpage(info, tweet_id):
        return False
    return str(info.get("display_id") or "") == tweet_id and bool(str(info.get("id") or "").strip())


def _valid_x_extractor_and_webpage(info: dict[str, Any] | None, tweet_id: str) -> bool:
    if not isinstance(info, dict):
        return False
    extractors = [str(info.get(key) or "").lower() for key in ("extractor", "extractor_key")]
    if not any(value.startswith("twitter") for value in extractors):
        return False
    if any(value and not value.startswith("twitter") for value in extractors):
        return False
    webpage = parse_canonical_x_status_url(str(info.get("webpage_url") or ""))
    return webpage is not None and webpage.tweet_id == tweet_id


def _provenance(info: dict[str, Any], tweet_id: str, version: str | None, info_sha256: str) -> dict[str, Any]:
    extractor = str(info.get("extractor_key") or info.get("extractor") or "")[:80]
    payload: dict[str, Any] = {
        "strategy": "yt_dlp",
        "tweet_id": tweet_id,
        "extractor": extractor,
        "version": version,
        "media_id": str(info["id"]),
        "uploader": str(info.get("uploader") or "")[:200] or None,
        "timestamp": info.get("timestamp"),
        "info_json_sha256": info_sha256,
    }
    return payload


def _persist_validated_media(job_dir: Path, assets_dir: Path, tweet_id: str) -> list[dict[str, Any]]:
    destination = assets_dir / "tweets"
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o700)
    files: list[dict[str, Any]] = []
    for source in sorted(job_dir.iterdir()):
        if not source.is_file() or source.name.endswith(".info.json"):
            continue
        media_type = _MEDIA_EXTENSIONS.get(source.suffix.lower())
        if media_type is None or source.stat().st_size > _MAX_MEDIA_BYTES:
            continue
        kind, mime = media_type
        if not looks_like_media_file(source, kind):
            continue
        digest = _sha256_file(source)
        target = destination / f"{tweet_id}_{digest[:16]}{source.suffix.lower()}"
        shutil.move(str(source), target)
        target.chmod(0o600)
        files.append(
            {
                "path": target,
                "relative_path": str(target.relative_to(assets_dir.parent)),
                "kind": kind,
                "mime": mime,
                "size": target.stat().st_size,
                "content_hash": digest,
            }
        )
    return files


def _secure_job_files(job_dir: Path) -> None:
    for path in job_dir.rglob("*"):
        if path.is_file():
            path.chmod(0o600)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _failure(
    reason: str,
    *,
    version: str | None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
) -> XFallbackResult:
    evidence: dict[str, Any] = {"strategy": "yt_dlp", "version": version, "reason": reason}
    if exit_code is not None:
        evidence["exit_code"] = exit_code
    if stdout:
        evidence["stdout"] = _redact_diagnostic(stdout)
    if stderr:
        evidence["stderr"] = _redact_diagnostic(stderr)
    return XFallbackResult("failed", [], reason, {}, evidence)


def _redact_diagnostic(value: str) -> str:
    text = re.sub(
        r"https?://[^\s]+",
        lambda match: _url_without_query(match.group(0)),
        value,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?i)(authorization|cookie|set-cookie|x-api-key)\s*[:=]\s*[^\r\n]*",
        r"\1=[REDACTED]",
        text,
    )
    home = str(Path.home())
    if home:
        text = text.replace(home, "~")
    return text[:_DIAGNOSTIC_LIMIT]


def _url_without_query(value: str) -> str:
    trailing = value[-1] if value and value[-1] in ",.;)]" else ""
    raw = value[:-1] if trailing else value
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        port = parsed.port
        netloc = f"{hostname}:{port}" if port is not None else hostname
        redacted = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except ValueError:
        return "[REDACTED_URL]"
    return redacted + trailing
