"""Retention cleanup for intermediate WAV files after durable transcription."""
from __future__ import annotations

from backend.domain.timestamps import utc_now

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.infrastructure.storage.audio_retention import (
    AudioRetentionRepository,
)
from backend.infrastructure.storage.paths import resolve_data_dir


DEFAULT_AUDIO_RETENTION_DAYS = 7
DEFAULT_AUDIO_RETENTION_BATCH = 500




def _iso(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _configured_integer(name: str, default: int, *, maximum: int) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, min(value, maximum))


def _empty_result(retention_days: int, *, enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "retention_days": retention_days,
        "scanned": 0,
        "deleted": 0,
        "missing": 0,
        "skipped": 0,
        "bytes_reclaimed": 0,
        "errors": [],
    }


def _safe_audio_path(audio_root: Path, raw_path: str) -> tuple[Path | None, str]:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        return None, "path_not_absolute"
    if candidate.suffix.lower() != ".wav":
        return None, "not_wav"
    if candidate.is_symlink():
        return None, "symlink_rejected"

    resolved_root = audio_root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        return None, "outside_audio_root"
    if resolved_candidate == resolved_root:
        return None, "audio_root_rejected"
    return resolved_candidate, ""


def cleanup_retained_audio(
    base_dir: Path | str,
    *,
    now: datetime | None = None,
    retention_days: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Delete old intermediate WAVs only when durable task evidence is safe."""
    days = (
        _configured_integer(
            "INFOOS_AUDIO_RETENTION_DAYS",
            DEFAULT_AUDIO_RETENTION_DAYS,
            maximum=3650,
        )
        if retention_days is None
        else max(0, min(int(retention_days), 3650))
    )
    if days == 0:
        return _empty_result(days, enabled=False)

    batch_limit = (
        _configured_integer(
            "INFOOS_AUDIO_RETENTION_BATCH",
            DEFAULT_AUDIO_RETENTION_BATCH,
            maximum=5000,
        )
        if limit is None
        else max(1, min(int(limit), 5000))
    )
    if batch_limit == 0:
        return _empty_result(days, enabled=True)

    current = (now or utc_now()).astimezone(timezone.utc)
    cutoff = current - timedelta(days=days)
    processed_at = _iso(current)
    repository = AudioRetentionRepository(base_dir)
    candidates = repository.list_candidates(
        completed_before=_iso(cutoff),
        limit=batch_limit,
    )
    result = _empty_result(days, enabled=True)
    result["scanned"] = len(candidates)
    audio_root = resolve_data_dir(base_dir) / "audio"

    for candidate in candidates:
        raw_path = str(candidate.get("audio_path") or "")
        audio_path, rejection = _safe_audio_path(audio_root, raw_path)
        if audio_path is None:
            result["skipped"] += 1
            result["errors"].append(
                {
                    "audio_file": Path(raw_path).name,
                    "reason": rejection,
                }
            )
            continue

        if not audio_path.exists():
            repository.record_disposition(
                audio_path=raw_path,
                representative_task_id=str(candidate["task_id"]),
                status="missing",
                processed_at=processed_at,
                bytes_reclaimed=0,
                retention_days=days,
            )
            result["missing"] += 1
            continue

        try:
            file_stat = audio_path.stat()
            modified_at = datetime.fromtimestamp(
                file_stat.st_mtime,
                tz=timezone.utc,
            )
            if modified_at > cutoff:
                result["skipped"] += 1
                continue
            bytes_reclaimed = int(file_stat.st_size)
            audio_path.unlink()
            repository.record_disposition(
                audio_path=raw_path,
                representative_task_id=str(candidate["task_id"]),
                status="deleted",
                processed_at=processed_at,
                bytes_reclaimed=bytes_reclaimed,
                retention_days=days,
            )
        except OSError as exc:
            result["skipped"] += 1
            result["errors"].append(
                {
                    "audio_file": audio_path.name,
                    "reason": f"{type(exc).__name__}:{exc.errno}",
                }
            )
            continue

        result["deleted"] += 1
        result["bytes_reclaimed"] += bytes_reclaimed

    return result
