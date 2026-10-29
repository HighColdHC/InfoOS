"""Shared safe byte-range helpers for authenticated product asset routes."""
from __future__ import annotations

from collections.abc import Iterator
import mimetypes
from pathlib import Path


_SAFE_MIME_PREFIXES = ("video/", "audio/")
_SAFE_EXACT_MIME = frozenset(
    {
        "image/avif",
        "image/bmp",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/plain",
        "application/pdf",
        "application/json",
        "application/octet-stream",
    }
)


def safe_media_type(path: Path, declared: str | None) -> str:
    value = (declared or "").strip().lower()
    if (
        value
        and "\n" not in value
        and "\r" not in value
        and (value.startswith(_SAFE_MIME_PREFIXES) or value in _SAFE_EXACT_MIME)
    ):
        return value
    guessed = (mimetypes.guess_type(path.name)[0] or "").lower()
    if guessed.startswith(_SAFE_MIME_PREFIXES) or guessed in _SAFE_EXACT_MIME:
        return guessed
    return "application/octet-stream"


def parse_single_range(value: str, size: int) -> tuple[int, int]:
    if size <= 0 or not value.startswith("bytes=") or "," in value:
        raise ValueError("unsupported range")
    raw = value.removeprefix("bytes=").strip()
    if raw.count("-") != 1:
        raise ValueError("invalid range")
    start_text, end_text = raw.split("-", 1)
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                raise ValueError("invalid suffix")
            return max(0, size - suffix), size - 1
        start = int(start_text)
        if start < 0 or start >= size:
            raise ValueError("invalid start")
        end = size - 1 if not end_text else min(int(end_text), size - 1)
        if end < start:
            raise ValueError("invalid end")
        return start, end
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid range") from exc


def file_range(path: Path, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(64 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
