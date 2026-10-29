"""Immutable evidence for source capability calibration."""
from __future__ import annotations

import sqlite3
from typing import Any

from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now


class SourceCalibrationRepository(InfoOSRepository):
    """Persist and read source-specific calibration evidence."""

    def append(
        self,
        *,
        calibration_id: str,
        source_id: str,
        platform: str,
        source_type: str,
        identifier: str,
        result: str,
        task_id: str,
        attempt: int = 1,
        visible_count: int = 0,
        sample_count: int = 0,
        failure_class: str | None = None,
        error_summary: str | None = None,
        calibrated_at: str | None = None,
        raw_payload: dict[str, Any] | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        values = {
            "calibration_id": calibration_id,
            "source_id": source_id,
            "platform": platform,
            "source_type": source_type,
            "identifier": identifier,
            "result": result,
            "task_id": task_id,
            "attempt": attempt,
            "visible_count": visible_count,
            "sample_count": sample_count,
            "failure_class": failure_class,
            "error_summary": error_summary,
            "calibrated_at": calibrated_at,
            "raw_payload": raw_payload,
        }
        if conn is not None:
            return self.append_to_connection(conn, **values)
        with self.connect() as own_conn:
            return self.append_to_connection(own_conn, **values)

    @staticmethod
    def append_to_connection(
        conn: sqlite3.Connection,
        *,
        calibration_id: str,
        source_id: str,
        platform: str,
        source_type: str,
        identifier: str,
        result: str,
        task_id: str,
        attempt: int = 1,
        visible_count: int = 0,
        sample_count: int = 0,
        failure_class: str | None = None,
        error_summary: str | None = None,
        calibrated_at: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> str:
        conn.execute(
            """
            insert into source_calibrations (
              calibration_id, source_id, platform, source_type, identifier,
              result, visible_count, sample_count, failure_class, error_summary,
              task_id, attempt, calibrated_at, raw_payload
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                calibration_id,
                source_id,
                platform,
                source_type,
                identifier,
                result,
                max(0, int(visible_count)),
                max(0, int(sample_count)),
                failure_class,
                error_summary,
                task_id,
                max(1, int(attempt)),
                calibrated_at or utc_now(),
                dumps(raw_payload or {}),
            ),
        )
        return calibration_id

    def latest_for_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                """
                select * from source_calibrations
                where source_id = ?
                order by calibrated_at desc, rowid desc
                limit 1
                """,
                (source_id,),
            ).fetchone()
        return _row_payload(row)

    def get_by_task_id(self, task_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                "select * from source_calibrations where task_id = ? order by attempt desc limit 1",
                (task_id,),
            ).fetchone()
        return _row_payload(row)


def _row_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row) | {"raw_payload": loads(row["raw_payload"], {})}
