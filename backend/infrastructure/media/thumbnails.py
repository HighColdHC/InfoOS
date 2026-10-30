"""Render bounded WebP thumbnails using the local ffmpeg executable."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import subprocess
from typing import Literal

THUMBNAIL_MAX_EDGE = 640
THUMBNAIL_TIMEOUT_SECONDS = 30
THUMBNAILS_DIRNAME = "_thumbnails"


class ThumbnailError(Exception):
    """A thumbnail could not be produced; ``code`` is stored as error_code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RenderedThumbnail:
    width: int
    height: int
    size_bytes: int


def thumbnail_relative_path(asset_id: str) -> str:
    digest = hashlib.sha256(asset_id.encode("utf-8")).hexdigest()
    return f"{THUMBNAILS_DIRNAME}/{digest[:2]}/{digest}.webp"


def _webp_dimensions(header: bytes) -> tuple[int, int]:
    if len(header) < 30 or header[0:4] != b"RIFF" or header[8:12] != b"WEBP":
        raise ThumbnailError("invalid_output")
    chunk = header[12:16]
    if chunk == b"VP8 ":
        width = int.from_bytes(header[26:28], "little") & 0x3FFF
        height = int.from_bytes(header[28:30], "little") & 0x3FFF
    elif chunk == b"VP8L":
        bits = int.from_bytes(header[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
    elif chunk == b"VP8X":
        width = int.from_bytes(header[24:27], "little") + 1
        height = int.from_bytes(header[27:30], "little") + 1
    else:
        raise ThumbnailError("invalid_output")
    return width, height


def render_thumbnail(source: Path, target: Path, *, kind: Literal["image", "video"]) -> RenderedThumbnail:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ThumbnailError("ffmpeg_missing")
    if kind == "video":
        _run_ffmpeg(ffmpeg, source, target, ["-ss", "1"])
        try:
            return _rendered(target)
        except ThumbnailError:
            # Seeking past a short video's end exits 0 with an empty or stub file.
            _run_ffmpeg(ffmpeg, source, target, ["-ss", "0"])
    else:
        _run_ffmpeg(ffmpeg, source, target, [])
    return _rendered(target)


def _run_ffmpeg(ffmpeg: str, source: Path, target: Path, seek: list[str]) -> None:
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *seek,
        "-i", str(source), "-frames:v", "1", "-vf",
        f"scale='min({THUMBNAIL_MAX_EDGE},iw)':'min({THUMBNAIL_MAX_EDGE},ih)':force_original_aspect_ratio=decrease",
        "-c:v", "libwebp", "-quality", "75", "-f", "webp", str(target),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=THUMBNAIL_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ThumbnailError("ffmpeg_timeout") from exc
    except OSError as exc:
        raise ThumbnailError("ffmpeg_failed") from exc
    if result.returncode != 0:
        raise ThumbnailError("ffmpeg_failed")


def _rendered(target: Path) -> RenderedThumbnail:
    if not target.is_file() or target.stat().st_size == 0:
        raise ThumbnailError("empty_output")
    with target.open("rb") as stream:
        width, height = _webp_dimensions(stream.read(30))
    return RenderedThumbnail(width, height, target.stat().st_size)
