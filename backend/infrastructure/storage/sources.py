"""Source repository.

sources — module migrated to infoos in PR 4.23.
The corresponding infoos/storage/sources.py was deleted in this commit.
This file is now the sole source of truth.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, TypedDict, cast

from backend.domain.json_types import JsonObject
from backend.domain.sources.calibration import normalize_source_raw_metadata
from backend.domain.sources.capability import resolve_source_capability
from backend.domain.sources.status import SourceStatus
from backend.domain.sources.versioning import source_fetch_input_version
from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now
from backend.infrastructure.storage.source_config_versions import SourceConfigVersionRepository


_CONFIG_CHANGE_REASONS = frozenset(
    {
        "created",
        "fetch_config_changed",
        "display_changed",
        "schedule_changed",
        "activation_changed",
        "ai_processing_changed",
    }
)


class SourceRow(TypedDict):
    """One ``sources`` row; nullability follows the v0001 schema."""

    source_id: str
    source_scope: str
    platform: str
    source_type: str
    identifier: str
    display_name: str
    default_domain_id: str | None
    cadence_hours: float
    notes: str | None
    enabled: bool
    connector_status: str
    connector_id: str | None
    raw_metadata: JsonObject
    calibration_input_version: str | None
    next_run_at: str | None
    last_run_at: str | None
    created_at: str
    updated_at: str


class SourceRepository(InfoOSRepository):
    def upsert_source(self, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            _upsert_source_to_connection(conn, payload, preserve_default_domain=True)

    def save_configuration(
        self,
        payload: dict[str, Any],
        *,
        change_reason: str,
        connection: sqlite3.Connection | None = None,
    ) -> SourceRow:
        """Save current state and immutable config evidence in one transaction."""
        _validate_change_reason(change_reason)
        normalized = {**payload, "default_domain_id": None}
        if connection is not None:
            return self._save_configuration_to_connection(
                connection,
                normalized,
                change_reason=change_reason,
            )
        with self.connect() as conn:
            return self._save_configuration_to_connection(
                conn,
                normalized,
                change_reason=change_reason,
            )

    @staticmethod
    def _save_configuration_to_connection(
        conn: sqlite3.Connection,
        payload: dict[str, Any],
        *,
        change_reason: str,
    ) -> SourceRow:
        source = _upsert_source_to_connection(conn, payload, preserve_default_domain=False)
        SourceConfigVersionRepository.append_to_connection(
            conn,
            source,
            change_reason=change_reason,
        )
        return source

    def set_enabled(
        self,
        source_id: str,
        enabled: bool,
        *,
        connection: sqlite3.Connection | None = None,
        change_reason: str = "activation_changed",
    ) -> SourceRow | None:
        _validate_change_reason(change_reason)
        if connection is not None:
            return self._set_enabled_to_connection(
                connection,
                source_id,
                enabled,
                change_reason=change_reason,
            )
        with self.connect() as conn:
            return self._set_enabled_to_connection(
                conn,
                source_id,
                enabled,
                change_reason=change_reason,
            )

    def _set_enabled_to_connection(
        self,
        conn: sqlite3.Connection,
        source_id: str,
        enabled: bool,
        *,
        change_reason: str,
    ) -> SourceRow | None:
        row = conn.execute("select * from sources where source_id = ?", (source_id,)).fetchone()
        if row is None or _system_managed_row(row):
            return None
        payload = _source_row(row) | {"enabled": bool(enabled)}
        return self._save_configuration_to_connection(
            conn,
            payload,
            change_reason=change_reason,
        )

    def find_by_stable_identity(
        self,
        platform: str,
        source_type: str,
        identifier: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> SourceRow | None:
        if connection is None:
            with self.connect_existing() as conn:
                return self.find_by_stable_identity(platform, source_type, identifier, connection=conn)
        row = connection.execute(
            """
            select * from sources
            where lower(trim(platform)) = lower(trim(?))
              and lower(trim(source_type)) = lower(trim(?))
              and lower(trim(identifier)) = lower(trim(?))
            order by created_at, source_id
            limit 1
            """,
            (platform, source_type, identifier),
        ).fetchone()
        return _source_row(row) if row is not None else None

    def list_versions(self, source_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return SourceConfigVersionRepository(self.base_dir, db_path=self.db_path).list_versions(
            source_id,
            limit,
        )

    def list_sources(
        self,
        *,
        include_disabled: bool = True,
        include_research: bool = False,
    ) -> list[SourceRow]:
        """List maintained sources by default; runtime research sources stay internal.

        SQLite is the single source of truth for source configuration.
        """
        sql = "select * from sources"
        if not include_research:
            sql += " where source_scope = 'maintained'"
        sql += " order by display_name"
        with self.connect_existing() as conn:
            rows = conn.execute(sql).fetchall()
        result = [_source_row(row) for row in rows]
        if not include_disabled:
            result = [row for row in result if row["enabled"]]
        return result

    def get_source(
        self,
        source_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> SourceRow | None:
        if connection is not None:
            row = connection.execute(
                "select * from sources where source_id = ?", (source_id,)
            ).fetchone()
        else:
            with self.connect_existing() as conn:
                row = conn.execute(
                    "select * from sources where source_id = ?", (source_id,)
                ).fetchone()
        return _source_row(row) if row else None

    def get_maintained_source(
        self,
        source_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> SourceRow | None:
        if connection is not None:
            row = connection.execute(
                "select * from sources where source_id = ? and source_scope = 'maintained'",
                (source_id,),
            ).fetchone()
        else:
            with self.connect_existing() as conn:
                row = conn.execute(
                    "select * from sources where source_id = ? and source_scope = 'maintained'",
                    (source_id,),
                ).fetchone()
        return _source_row(row) if row else None

    def update_source(self, source_id: str, *, cadence_hours: float | None = None) -> dict[str, Any] | None:
        """Update mutable source fields. Returns the updated row, or None if missing.

        Currently supports ``cadence_hours`` updates through SQLite only.
        """
        existing = self.get_source(source_id)
        if existing is None or bool((existing.get("raw_metadata") or {}).get("system_managed")):
            return None
        sets: list[str] = []
        params: list[Any] = []
        if cadence_hours is not None:
            sets.append("cadence_hours = ?")
            params.append(float(cadence_hours))
        if not sets:
            return self.get_source(source_id)
        sets.append("updated_at = ?")
        params.append(utc_now())
        params.append(source_id)
        with self.connect() as conn:
            conn.execute(
                f"update sources set {', '.join(sets)} where source_id = ?",
                params,
            )
            conn.commit()
        return self.get_source(source_id)

    def delete_source(self, source_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "select raw_metadata from sources where source_id = ?",
                (source_id,),
            ).fetchone()
            if row is None or _system_managed_row(row):
                return False
            for table in ("run_history", "raw_items", "cards", "capture_receipts"):
                if conn.execute(f"select 1 from {table} where source_id = ? limit 1", (source_id,)).fetchone():
                    raise sqlite3.IntegrityError("source_has_history")
            if conn.execute(
                "select 1 from tasks where target_kind = 'source' and target_id = ? and (started_at is not null or status <> 'pending') limit 1",
                (source_id,),
            ).fetchone():
                raise sqlite3.IntegrityError("source_has_history")
            if conn.execute(
                """select 1 from tasks t join task_batch_items i on i.task_id=t.task_id
                   where t.target_kind='source' and t.target_id=? limit 1""", (source_id,),
            ).fetchone():
                raise sqlite3.IntegrityError("source_has_history")
            pending = "select task_id from tasks where target_kind = 'source' and target_id = ? and status = 'pending'"
            conn.execute(f"delete from task_events where task_id in ({pending})", (source_id,))
            conn.execute(f"delete from task_batch_items where task_id in ({pending})", (source_id,))
            conn.execute("delete from tasks where target_kind = 'source' and target_id = ? and status = 'pending'", (source_id,))
            for table in ("source_config_versions", "source_cursors", "source_interpretation_profile_bindings"):
                conn.execute(f"delete from {table} where source_id = ?", (source_id,))
            conn.execute("delete from source_calibrations where source_id = ?", (source_id,))
            cursor = conn.execute("delete from sources where source_id = ?", (source_id,))
            conn.commit()
        return cursor.rowcount > 0

    def record_calibration_result(
        self,
        *,
        calibration: dict[str, Any],
        connector_status: str,
        expected_source: dict[str, Any] | None = None,
        on_recorded: Callable[[sqlite3.Connection], None] | None = None,
    ) -> bool:
        """Commit immutable calibration evidence and the source status together."""
        from backend.infrastructure.storage.source_calibrations import SourceCalibrationRepository

        with self.connect() as conn:
            conn.execute("begin immediate")
            current = conn.execute(
                "select * from sources where source_id = ?",
                (calibration["source_id"],),
            ).fetchone()
            if current is None:
                raise KeyError(f"source not found: {calibration['source_id']}")
            if expected_source is not None and not _same_calibration_input(current, expected_source):
                conn.rollback()
                return False
            SourceCalibrationRepository.append_to_connection(conn, **calibration)
            conn.execute(
                """
                update sources
                set connector_status = ?, updated_at = ?
                where source_id = ?
                """,
                (connector_status, utc_now(), calibration["source_id"]),
            )
            if on_recorded is not None:
                on_recorded(conn)
            conn.commit()
        return True

def _validate_change_reason(change_reason: str) -> None:
    if change_reason not in _CONFIG_CHANGE_REASONS:
        allowed = ", ".join(sorted(_CONFIG_CHANGE_REASONS))
        raise ValueError(f"invalid change_reason; expected one of: {allowed}")


def _upsert_source_to_connection(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    preserve_default_domain: bool,
) -> SourceRow:
    connector_status = payload.get("connector_status") or (
        resolve_source_capability(payload).status
        or SourceStatus.IMPLEMENTED_UNCALIBRATED.value
    )
    now = utc_now()
    raw_metadata = normalize_source_raw_metadata(payload.get("raw_metadata"))
    normalized_payload = {**payload, "raw_metadata": raw_metadata}
    data = {
        "source_id": payload["source_id"],
        "source_scope": payload.get("source_scope") or "maintained",
        "platform": payload["platform"],
        "source_type": payload["source_type"],
        "identifier": payload["identifier"],
        "display_name": payload.get("display_name") or payload["source_id"],
        "default_domain_id": payload.get("default_domain_id") if preserve_default_domain else None,
        "cadence_hours": float(payload["cadence_hours"]),
        "notes": payload.get("notes"),
        "enabled": 1 if payload.get("enabled", True) else 0,
        "connector_status": connector_status,
        "connector_id": payload.get("connector_id"),
        "raw_metadata": dumps(raw_metadata),
        "calibration_input_version": source_fetch_input_version(normalized_payload),
        "next_run_at": payload.get("next_run_at"),
        "last_run_at": payload.get("last_run_at"),
        "created_at": payload.get("created_at", now),
        "updated_at": now,
    }
    previous = conn.execute(
        "select * from sources where source_id = ?",
        (data["source_id"],),
    ).fetchone()
    conn.execute(
        """
        insert into sources (
          source_id, source_scope, platform, source_type, identifier, display_name,
          default_domain_id, cadence_hours, notes, enabled, connector_status,
          connector_id, raw_metadata, calibration_input_version,
          next_run_at, last_run_at, created_at, updated_at
        ) values (
          :source_id, :source_scope, :platform, :source_type, :identifier, :display_name,
          :default_domain_id, :cadence_hours, :notes, :enabled, :connector_status,
          :connector_id, :raw_metadata, :calibration_input_version,
          :next_run_at, :last_run_at, :created_at, :updated_at
        )
        on conflict(source_id) do update set
          platform=excluded.platform,
          source_scope=excluded.source_scope,
          source_type=excluded.source_type,
          identifier=excluded.identifier,
          display_name=excluded.display_name,
          default_domain_id=excluded.default_domain_id,
          cadence_hours=excluded.cadence_hours,
          notes=excluded.notes,
          enabled=excluded.enabled,
          connector_status=excluded.connector_status,
          connector_id=excluded.connector_id,
          raw_metadata=excluded.raw_metadata,
          calibration_input_version=excluded.calibration_input_version,
          next_run_at=excluded.next_run_at,
          last_run_at=excluded.last_run_at,
          updated_at=excluded.updated_at
        """,
        data,
    )
    if previous is not None and str(previous["calibration_input_version"] or "") != data["calibration_input_version"]:
        _invalidate_stale_fetches(
            conn,
            str(data["source_id"]),
            current_version=str(data["calibration_input_version"]),
            now=now,
        )
    row = conn.execute("select * from sources where source_id = ?", (data["source_id"],)).fetchone()
    if row is None:
        raise sqlite3.IntegrityError("source configuration was not stored")
    return _source_row(row)


def _source_row(row: sqlite3.Row) -> SourceRow:
    return cast(SourceRow, dict(row) | {
        "enabled": bool(row["enabled"]),
        "raw_metadata": _metadata(row["raw_metadata"]),
    })


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    decoded = loads(value, {}) if isinstance(value, str) else {}
    return decoded if isinstance(decoded, dict) else {}


def _system_managed_row(row: Any) -> bool:
    return bool(_metadata(row["raw_metadata"]).get("system_managed"))


def _same_calibration_input(current: Any, expected: dict[str, Any]) -> bool:
    return all(
        current[key] == expected.get(key)
        for key in ("platform", "source_type", "identifier", "connector_id")
    ) and bool(current["enabled"]) == bool(expected.get("enabled", True)) and _metadata(
        current["raw_metadata"]
    ) == _metadata(expected.get("raw_metadata"))


def _invalidate_stale_fetches(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    current_version: str,
    now: str,
) -> None:
    rows = conn.execute(
        """
        select task_id, artifacts_summary from tasks
        where task_type = 'fetch' and target_kind = 'source' and target_id = ?
          and input_version <> ? and status in ('pending', 'running')
        """,
        (source_id, current_version),
    ).fetchall()
    for row in rows:
        artifacts = _metadata(row["artifacts_summary"])
        artifacts["reason"] = "source_configuration_changed"
        conn.execute(
            """
            update tasks set status = 'skipped', error_reason = ?, artifacts_summary = ?,
              claimed_by = null, lease_expires_at = null, heartbeat_at = null,
              finished_at = ?, updated_at = ?
            where task_id = ? and input_version <> ? and status in ('pending', 'running')
            """,
            ("source_configuration_changed", dumps(artifacts), now, now, row["task_id"], current_version),
        )
