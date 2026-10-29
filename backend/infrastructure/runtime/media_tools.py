"""Passive discovery of optional local media tools."""
from __future__ import annotations

import shutil


def media_tool_status() -> dict[str, dict[str, str | None]]:
    result: dict[str, dict[str, str | None]] = {}
    for key, executable in {"yt_dlp": "yt-dlp", "gallery_dl": "gallery-dl", "ffmpeg": "ffmpeg"}.items():
        path = shutil.which(executable)
        result[key] = {"status": "installed" if path else "missing", "path": path}
    return result
