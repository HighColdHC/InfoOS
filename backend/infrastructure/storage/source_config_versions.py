"""Immutable, non-secret source configuration versions."""
from __future__ import annotations

from hashlib import sha256
import json
import sqlite3
from typing import Any

from backend.domain.sources.versioning import source_fetch_input_version
from backend.domain.sources.feed_content import versioned_feed_policy
from backend.infrastructure.storage.base import InfoOSRepository, utc_now


_FETCH_CONFIG_KEYS = (
    "feed_url",
    "command",
    "items_path",
    "field_mapping",
    "metadata_mapping",
    "result_order",
    "requires_browser_session",
)


def canonical_config_version(source: dict[str, Any]) -> str:
    """Hash every saveable non-secret source configuration field."""
    encoded = _canonical_json(_safe_config(source)).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


class SourceConfigVersionRepository(InfoOSRepository):
    """Append and read immutable configuration snapshots."""

    @staticmethod
    def append_to_connection(
        conn: sqlite3.Connection,
        source: dict[str, Any],
        *,
        change_reason: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        source_id = str(source.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("source_id is required")
        reason = str(change_reason).strip()
        if not reason:
            raise ValueError("change_reason is required")

        config = _safe_config(source)
        config_json = _canonical_json(config)
        config_version = f"sha256:{sha256(config_json.encode('utf-8')).hexdigest()}"
        fetch_input_version = source_fetch_input_version(source)
        version_id = _stable_version_id(source_id, config_version)
        conn.execute(
            """
            insert or ignore into source_config_versions (
              version_id, source_id, config_version, fetch_input_version,
              config_json, change_reason, created_at
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                source_id,
                config_version,
                fetch_input_version,
                config_json,
                reason,
                created_at or utc_now(),
            ),
        )
        row = conn.execute(
            """
            select version_id, source_id, config_version, fetch_input_version,
              config_json, change_reason, created_at
            from source_config_versions
            where source_id = ? and config_version = ?
            """,
            (source_id, config_version),
        ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError("source configuration version was not stored")
        return _row_payload(row)

    def list_versions(self, source_id: str, limit: int) -> list[dict[str, Any]]:
        bounded_limit = max(0, int(limit))
        if bounded_limit == 0:
            return []
        with self.connect_existing() as conn:
            rows = conn.execute(
                """
                select version_id, source_id, config_version, fetch_input_version,
                  config_json, change_reason, created_at
                from source_config_versions
                where source_id = ?
                order by created_at desc, rowid desc
                limit ?
                """,
                (source_id, bounded_limit),
            ).fetchall()
        return [_row_payload(row) for row in rows]


def _safe_config(source: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = _raw_metadata(source.get("raw_metadata"))
    payload: dict[str, Any] = {
        "platform": _normalized_string(source.get("platform")),
        "source_type": _normalized_string(source.get("source_type")),
        "identifier": _normalized_string(source.get("identifier")),
        "display_name": _normalized_string(source.get("display_name")),
        "notes": _normalized_string(source.get("notes")),
        "cadence_hours": _normalized_float(source.get("cadence_hours")),
        "enabled": _normalized_boolean(source.get("enabled"), default=True),
        "connector_id": _normalized_string(source.get("connector_id") or source.get("connector")),
        "max_items_per_run": _normalized_integer(
            _source_or_metadata(source, raw_metadata, "max_items_per_run")
        ),
        "auto_enrich_media": _normalized_boolean(
            _source_or_metadata(source, raw_metadata, "auto_enrich_media"),
            default=True,
        ),
        "ai_processing": _safe_ai_processing(raw_metadata.get("ai_processing")),
    }
    payload.update(
        versioned_feed_policy(
            source.get("platform"),
            source.get("source_type"),
            raw_metadata,
        )
    )
    for key in _FETCH_CONFIG_KEYS:
        value = _source_or_metadata(source, raw_metadata, key)
        if key == "requires_browser_session":
            value = _normalized_boolean(value, default=False)
        elif key in {"feed_url", "items_path", "result_order"}:
            value = _normalized_string(value)
        payload[key] = value
    research = _source_or_metadata(source, raw_metadata, "research")
    if research is not None:
        payload["research"] = research

    detail_command = _source_or_metadata(source, raw_metadata, "detail_command")
    if detail_command is None:
        detail_command = _source_or_metadata(source, raw_metadata, "detail_command_template")
    payload["detail_command"] = detail_command
    return payload


def _safe_ai_processing(value: Any) -> dict[str, Any] | None:
    """Keep the normalized, non-secret source AI binding in immutable history."""
    if not isinstance(value, dict):
        return None
    return {
        "enabled": _normalized_boolean(value.get("enabled"), default=False),
        "provider_id": _normalized_string(value.get("provider_id")) or "",
        "model": _normalized_string(value.get("model")) or "",
        "prompt": _normalized_string(value.get("prompt")) or "",
        "target_language": _normalized_string(value.get("target_language")) or "zh-CN",
    }


def _stable_version_id(source_id: str, config_version: str) -> str:
    digest = sha256(f"{source_id}:{config_version}".encode("utf-8")).hexdigest()
    return f"source_config_{digest}"


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _raw_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _source_or_metadata(source: dict[str, Any], raw_metadata: dict[str, Any], key: str) -> Any:
    if key in source and source[key] is not None:
        return source[key]
    return raw_metadata.get(key)


def _normalized_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def _normalized_integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_boolean(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "no", "off", ""}:
            return False
        if normalized in {"true", "1", "yes", "on"}:
            return True
    return bool(value)


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["config_json"] = json.loads(payload["config_json"])
    return payload
