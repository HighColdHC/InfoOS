"""Content-addressed pre-card media, shared by acquisition and offline repair."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from backend.infrastructure.external_docs.feishu_browser import (
    FeishuMediaBlob, _bounded_media_blobs, _MAX_AUTHENTICATED_VIDEO_BYTES,
)
from backend.infrastructure.storage.paths import resolve_data_dir

_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "video/mp4": ".mp4", "video/quicktime": ".mov",
    "video/webm": ".webm",
}


class RawMediaStore:
    def __init__(self, data_dir: Path | str | None = None):
        self.root = (Path(data_dir) if data_dir is not None else resolve_data_dir()) / "assets" / "raw-media"

    def _path(self, reference: dict[str, Any]) -> Path:
        digest = str(reference.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid raw media sha256")
        extension = _EXTENSIONS.get(str(reference.get("mime_type") or ""), ".bin")
        path = self.root / digest[:2] / f"{digest}{extension}"
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("raw media path escapes root")
        return path

    def read(self, reference: dict[str, Any]) -> bytes:
        path = self._path(reference)
        size = reference.get("bytes")
        if type(size) is not int or not 0 < size <= _MAX_AUTHENTICATED_VIDEO_BYTES:
            raise ValueError("invalid raw media size")
        with path.open("rb") as stream:
            content = stream.read(size + 1)
        if len(content) != size or hashlib.sha256(content).hexdigest() != reference["sha256"]:
            raise ValueError("raw media size/hash mismatch")
        return content

    def put(self, blob: FeishuMediaBlob, *, write: bool = True) -> dict[str, Any]:
        reference = {"url": blob.url, "mime_type": blob.mime_type, "kind": blob.kind,
                     "sha256": hashlib.sha256(blob.content).hexdigest(), "bytes": len(blob.content)}
        if not write:
            return reference
        path = self._path(reference)
        if path.exists():
            self.read(reference)
            return reference
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".raw-media-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(blob.content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            # File bytes must be durable before a database reference is published.
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self.read(reference)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return reference

    def load(self, value: Any) -> list[FeishuMediaBlob]:
        """Both legacy inline values and references obey the browser limits."""
        return _bounded_media_blobs(value, read_reference=self.read)

    def externalize(self, payload: Any, *, write: bool = True) -> Any:
        """Copy a record without ever copying inline media into a second container."""
        if isinstance(payload, list):
            return [self.externalize(item, write=write) for item in payload]
        if not isinstance(payload, dict):
            return payload
        result = {}
        for key, value in payload.items():
            if key == "media_blobs":
                rejected: list[dict[str, Any]] = []
                blobs = _bounded_media_blobs(value, read_reference=self.read, rejected=rejected)
                result[key] = [self.put(blob, write=write) for blob in blobs] + rejected
            else:
                result[key] = self.externalize(value, write=write)
        return result
