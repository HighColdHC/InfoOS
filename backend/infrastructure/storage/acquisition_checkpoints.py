"""Committed acquisition checkpoint reads and caller-owned writes."""
from __future__ import annotations

import sqlite3
from typing import Any

from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now


class AcquisitionCheckpointRepository(InfoOSRepository):
    def get_for_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                "select * from acquisition_checkpoints where task_id = ?",
                (task_id,),
            ).fetchone()
        return self._row(row)

    def get_complete_matching(
        self,
        *,
        task_id: str,
        source_id: str,
        expected_source_version: str | None,
        expected_run_id: str,
    ) -> dict[str, Any] | None:
        checkpoint = self.get_for_task(task_id)
        if (
            checkpoint is None
            or checkpoint["source_id"] != source_id
            or checkpoint["run_id"] != expected_run_id
        ):
            return None
        if expected_source_version and checkpoint["source_input_version"] != expected_source_version:
            return None
        payload = checkpoint.get("payload")
        if not isinstance(payload, dict) or not _is_complete(checkpoint, payload):
            return None
        return checkpoint

    def upgrade_legacy_if_valid(self, *, task_id: str, payload: dict[str, Any]) -> bool:
        required = {
            "source_id", "run_id", "source_input_version", "initial_fetch",
            "calibration_evidence_id", "committed_last_run_at", "committed_next_run_at",
            "raw_item_ids", "card_build_task_ids", "cursor_after",
        }
        if not required.issubset(payload):
            return False
        with self.connect() as conn:
            conn.execute("begin immediate")
            if not _durable_facts_match(conn, task_id, payload):
                conn.rollback()
                return False
            self.upsert_to_connection(
                conn,
                task_id=task_id,
                source_id=str(payload["source_id"]),
                run_id=str(payload["run_id"]),
                source_input_version=str(payload["source_input_version"]),
                initial_fetch=bool(payload["initial_fetch"]),
                calibration_evidence_id=payload.get("calibration_evidence_id"),
                committed_last_run_at=str(payload["committed_last_run_at"]),
                committed_next_run_at=str(payload["committed_next_run_at"]),
                payload=payload,
            )
            conn.commit()
        return True

    @staticmethod
    def upsert_to_connection(
        conn: sqlite3.Connection,
        *,
        task_id: str,
        source_id: str,
        run_id: str,
        source_input_version: str,
        initial_fetch: bool,
        calibration_evidence_id: str | None,
        committed_last_run_at: str,
        committed_next_run_at: str,
        payload: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            insert into acquisition_checkpoints (
              task_id, source_id, run_id, source_input_version, initial_fetch,
              calibration_evidence_id, committed_last_run_at, committed_next_run_at,
              payload, committed_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(task_id) do update set
              source_id=excluded.source_id,
              run_id=excluded.run_id,
              source_input_version=excluded.source_input_version,
              initial_fetch=excluded.initial_fetch,
              calibration_evidence_id=excluded.calibration_evidence_id,
              committed_last_run_at=excluded.committed_last_run_at,
              committed_next_run_at=excluded.committed_next_run_at,
              payload=excluded.payload,
              committed_at=excluded.committed_at
            """,
            (
                task_id, source_id, run_id, source_input_version, int(initial_fetch),
                calibration_evidence_id, committed_last_run_at, committed_next_run_at,
                dumps(payload), utc_now(),
            ),
        )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["initial_fetch"] = bool(result["initial_fetch"])
        result["payload"] = loads(result["payload"], {})
        return result


def _is_complete(row: dict[str, Any], payload: dict[str, Any]) -> bool:
    required_payload = {
        "source_id", "run_id", "source_input_version", "initial_fetch",
        "calibration_evidence_id", "committed_last_run_at", "committed_next_run_at",
        "raw_item_ids", "card_build_task_ids", "cursor_after",
    }
    if not required_payload.issubset(payload):
        return False
    if any(row.get(key) in (None, "") for key in (
        "source_input_version", "run_id", "committed_last_run_at", "committed_next_run_at"
    )):
        return False
    if bool(row["initial_fetch"]) and not row.get("calibration_evidence_id"):
        return False
    return (
        payload["source_id"] == row["source_id"]
        and payload["run_id"] == row["run_id"]
        and payload["source_input_version"] == row["source_input_version"]
        and bool(payload["initial_fetch"]) == bool(row["initial_fetch"])
        and payload["calibration_evidence_id"] == row.get("calibration_evidence_id")
        and payload["committed_last_run_at"] == row["committed_last_run_at"]
        and payload["committed_next_run_at"] == row["committed_next_run_at"]
    )


def _durable_facts_match(conn: sqlite3.Connection, task_id: str, payload: dict[str, Any]) -> bool:
    run = conn.execute(
        "select source_id, finished_at, next_run_at from run_history where run_id = ?",
        (payload["run_id"],),
    ).fetchone()
    cursor = conn.execute(
        "select cursor_value, last_run_id from source_cursors where source_id = ?",
        (payload["source_id"],),
    ).fetchone()
    task_row = conn.execute("select 1 from tasks where task_id = ?", (task_id,)).fetchone()
    if (
        task_row is None or run is None or cursor is None
        or run["source_id"] != payload["source_id"]
        or run["finished_at"] != payload["committed_last_run_at"]
        or run["next_run_at"] != payload["committed_next_run_at"]
        or cursor["last_run_id"] != payload["run_id"]
        or cursor["cursor_value"] != payload["cursor_after"]
    ):
        return False
    for raw_item_id in payload["raw_item_ids"]:
        if conn.execute(
            "select 1 from raw_items where raw_item_id = ? and run_id = ?",
            (raw_item_id, payload["run_id"]),
        ).fetchone() is None:
            return False
    for child_task_id in payload["card_build_task_ids"]:
        if conn.execute("select 1 from tasks where task_id = ?", (child_task_id,)).fetchone() is None:
            return False
    evidence_id = payload.get("calibration_evidence_id")
    if bool(payload["initial_fetch"]) and (
        not evidence_id
        or conn.execute(
            "select 1 from source_calibrations where calibration_id = ? and task_id = ?",
            (evidence_id, task_id),
        ).fetchone() is None
    ):
        return False
    return True
