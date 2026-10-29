from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
from typing import Literal
import shutil
import subprocess
import sys

MediaToolStatus = Literal["downloaded", "skipped", "blocked", "failed"]

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}


@dataclass(frozen=True)
class DownloadedMediaFile:
    path: Path
    relative_path: str
    kind: Literal["image", "video"]
    mime: str
    size: int


@dataclass(frozen=True)
class MediaToolResult:
    status: MediaToolStatus
    files: list[DownloadedMediaFile]
    reason: str | None = None
    stderr: str = ""
    stdout: str = ""
    metadata: dict = field(default_factory=dict)


def run_yt_dlp_download(url: str, output_dir: Path, *, cookies_from_browser: str | None = None) -> MediaToolResult:
    executable = _resolve_media_tool("yt-dlp")
    if executable is None:
        return MediaToolResult(status="blocked", files=[], reason="yt_dlp_missing")
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "--ignore-config",
        "--no-playlist",
        "--write-info-json",
        "--socket-timeout", "15",
        "--retries", "2",
        "--fragment-retries", "2",
        "--max-filesize", str(200 * 1024 * 1024),
        "--write-thumbnail",
        "--convert-thumbnails",
        "jpg",
        "--js-runtimes",
        "node",
        "--remote-components",
        "ejs:npm",
        "-f",
        "bv*[height<=720]+ba/b[height<=720]/best[height<=720]/best",
        "--paths",
        str(output_dir),
        "-o",
        "%(id)s.%(ext)s",
        url,
    ]
    if cookies_from_browser:
        command[1:1] = ["--cookies-from-browser", cookies_from_browser]
    result = _run_media_command(command, output_dir=output_dir)
    metadata = dict(result.metadata)
    try:
        metadata["version"] = version("yt-dlp")
    except PackageNotFoundError:
        metadata["version"] = None
    return MediaToolResult(
        status=result.status,
        files=result.files,
        reason=result.reason,
        stderr=result.stderr,
        stdout=result.stdout,
        metadata=metadata,
    )


def run_gallery_dl_download(url: str, output_dir: Path, *, cookies_from_browser: str | None = None) -> MediaToolResult:
    executable = _resolve_media_tool("gallery-dl")
    if executable is None:
        return MediaToolResult(status="blocked", files=[], reason="gallery_dl_missing")
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [executable, "--dest", str(output_dir), url]
    if cookies_from_browser:
        command.extend(["--cookies-from-browser", cookies_from_browser])
    return _run_media_command(command, output_dir=output_dir)


def list_downloaded_media_files(output_dir: Path, *, relative_base: Path | None = None) -> list[DownloadedMediaFile]:
    base = relative_base or output_dir.parent
    files: list[DownloadedMediaFile] = []
    if not output_dir.exists():
        return files
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            kind = "image"
            mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else f"image/{suffix.lstrip('.')}"
        elif suffix in _VIDEO_EXTENSIONS:
            kind = "video"
            mime = "video/mp4" if suffix == ".mp4" else f"video/{suffix.lstrip('.')}"
        else:
            continue
        if not _looks_like_media_file(path, kind):
            continue
        files.append(
            DownloadedMediaFile(
                path=path,
                relative_path=str(path.relative_to(base)),
                kind=kind,
                mime=mime,
                size=path.stat().st_size,
            )
        )
    return files


def _resolve_media_tool(name: str) -> str | None:
    executable = shutil.which(name)
    if executable:
        return executable
    venv_candidate = Path(sys.executable).with_name(name)
    if venv_candidate.is_file():
        return str(venv_candidate)
    return None


def _run_media_command(command: list[str], *, output_dir: Path) -> MediaToolResult:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    except FileNotFoundError:
        return MediaToolResult(status="blocked", files=[], reason="tool_missing")
    except subprocess.TimeoutExpired:
        return MediaToolResult(status="failed", files=[], reason="timeout")
    for path in output_dir.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    metadata = _consume_info_json(output_dir)
    files = list_downloaded_media_files(output_dir, relative_base=output_dir.parent)
    if result.returncode == 0 and files:
        return MediaToolResult(status="downloaded", files=files, stdout=result.stdout, stderr=result.stderr, metadata=metadata)
    if result.returncode == 0:
        return MediaToolResult(status="skipped", files=[], reason="no_media_files", stdout=result.stdout, stderr=result.stderr)
    auth_required = _looks_auth_required(result.stderr)
    return MediaToolResult(
        status="blocked" if auth_required else "failed",
        files=[],
        reason="auth_required" if auth_required else "command_failed",
        stdout=result.stdout,
        stderr=result.stderr[-1000:],
        metadata=metadata,
    )


def _consume_info_json(output_dir: Path) -> dict:
    info_files = list(output_dir.rglob("*.info.json"))
    if len(info_files) != 1:
        for path in info_files:
            path.unlink(missing_ok=True)
        return {}
    path = info_files[0]
    raw = path.read_bytes()
    path.unlink(missing_ok=True)
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(info, dict):
        return {}
    return {"info": info, "info_json_sha256": hashlib.sha256(raw).hexdigest()}


def _looks_auth_required(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(
        marker in lowered
        for marker in ("401", "403", "unauthorized", "forbidden", "login", "log in", "auth_required", "network security")
    )


def _looks_like_media_file(path: Path, kind: str) -> bool:
    try:
        header = path.read_bytes()[:64]
    except OSError:
        return False
    stripped = header.lstrip().lower()
    if stripped.startswith((b"<!doctype html", b"<html", b"{", b"[")):
        return False
    if kind == "image":
        return header.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"RIFF"))
    if kind == "video":
        return b"ftyp" in header[:16] or header.startswith(b"\x1aE\xdf\xa3")
    return False
