"""Durable receipts for explicit browser capture observations."""
from __future__ import annotations

import sqlite3
from typing import Any, NotRequired, TypedDict, cast

from backend.infrastructure.storage.base import InfoOSRepository


class CaptureReceiptRow(TypedDict):
    capture_id: str
    client_capture_id: str
    token_id: str
    payload_hash: str
    canonical_identity_hash: str
    content_identity_hash: str | None
    observation_version: int
    capture_mode: str
    disposition: str
    processing_status: str
    canonical_url: str
    source_id: str
    run_id: str
    raw_connector_id: str
    raw_item_id: str
    task_id: str | None
    card_id: str | None
    error_code: str | None
    created_at: str
    updated_at: str


class CapturedCardRow(TypedDict):
    card_id: str
    status: str
    version: int
    content_hash: str | None
    updated_at: str
    source_run_id: str | None


class CaptureCountRow(TypedDict):
    count: int


class RootCaptureReceipt(TypedDict):
    capture_id: NotRequired[str]
    run_id: NotRequired[str]


class CaptureReceiptRepository(InfoOSRepository):
    def count_for_token(
        self, token_id: str, *, since: str, until: str,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        row = cast(CaptureCountRow, self._one(
            """select count(*) as count from capture_receipts
               where token_id = ? and created_at > ? and created_at <= ?""",
            (token_id, since, until), connection=connection,
        ))
        return int(row["count"])

    @staticmethod
    def finalize_root_to_connection(
        conn: sqlite3.Connection, task_id: str, *, status: str,
        error_code: str | None, finished_at: str,
    ) -> RootCaptureReceipt:
        receipt = conn.execute(
            "select capture_id, run_id, canonical_url from capture_receipts where task_id = ?",
            (task_id,),
        ).fetchone()
        if receipt is None:
            return {}
        if status in {"failed", "blocked", "skipped"}:
            conn.execute(
                """update run_history set status = ?, error_summary = ?, error_count = 1,
                   finished_at = ? where run_id = ? and status in ('pending', 'running')""",
                (status, error_code or "capture_processing_failed", finished_at, receipt["run_id"]),
            )
            card = conn.execute(
                "select card_id,source_run_id from cards where source_url = ? and status = 'active' order by updated_at desc limit 1",
                (receipt["canonical_url"],),
            ).fetchone()
            resolved = card is not None and card["source_run_id"] == receipt["run_id"]
            conn.execute(
                """update capture_receipts set processing_status = ?, error_code = ?,
                   card_id = coalesce(?, card_id), updated_at = ? where capture_id = ?""",
                ("succeeded" if resolved else "failed", None if resolved else error_code or "capture_processing_failed",
                 card["card_id"] if card else None, finished_at, receipt["capture_id"]),
            )
        return {"capture_id": receipt["capture_id"], "run_id": receipt["run_id"]}

    def get_for_client(
        self,
        token_id: str,
        client_capture_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> CaptureReceiptRow | None:
        return cast(CaptureReceiptRow | None, self._one(
            """
            select * from capture_receipts
            where token_id = ? and client_capture_id = ?
            """,
            (token_id, client_capture_id),
            connection=connection,
        ))

    def get_owned(self, capture_id: str, token_id: str) -> CaptureReceiptRow | None:
        return cast(CaptureReceiptRow | None, self._one(
            """
            select * from capture_receipts
            where capture_id = ? and token_id = ?
            """,
            (capture_id, token_id),
        ))

    def get(self, capture_id: str) -> CaptureReceiptRow | None:
        return cast(CaptureReceiptRow | None, self._one(
            "select * from capture_receipts where capture_id = ?",
            (capture_id,),
        ))

    def latest_for_identity(
        self,
        canonical_identity_hash: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> CaptureReceiptRow | None:
        return cast(CaptureReceiptRow | None, self._one(
            """
            select * from capture_receipts
            where canonical_identity_hash = ?
            order by observation_version desc
            limit 1
            """,
            (canonical_identity_hash,),
            connection=connection,
        ))

    def latest_before_observation(
        self,
        canonical_identity_hash: str,
        observation_version: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> CaptureReceiptRow | None:
        return cast(CaptureReceiptRow | None, self._one(
            """
            select * from capture_receipts
            where canonical_identity_hash = ? and observation_version < ?
            order by observation_version desc
            limit 1
            """,
            (canonical_identity_hash, observation_version),
            connection=connection,
        ))

    def card_for_url(
        self,
        canonical_url: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> CapturedCardRow | None:
        return cast(CapturedCardRow | None, self._one(
            """
            select card_id, status, version, content_hash, updated_at, source_run_id
            from cards
            where source_url = ?
            order by case status when 'active' then 0 else 1 end, updated_at desc
            limit 1
            """,
            (canonical_url,),
            connection=connection,
        ))

    @staticmethod
    def insert_to_connection(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
        conn.execute(
            """
            insert into capture_receipts (
              capture_id, client_capture_id, token_id, payload_hash,
              canonical_identity_hash, content_identity_hash, observation_version,
              capture_mode, disposition, processing_status, canonical_url,
              source_id, run_id, raw_connector_id, raw_item_id, task_id, card_id,
              error_code, created_at, updated_at
            ) values (
              :capture_id, :client_capture_id, :token_id, :payload_hash,
              :canonical_identity_hash, :content_identity_hash, :observation_version,
              :capture_mode, :disposition, :processing_status, :canonical_url,
              :source_id, :run_id, :raw_connector_id, :raw_item_id, :task_id, :card_id,
              :error_code, :created_at, :updated_at
            )
            """,
            payload,
        )

    def update_processing(
        self,
        capture_id: str,
        *,
        processing_status: str,
        disposition: str | None = None,
        content_identity_hash: str | None = None,
        card_id: str | None = None,
        error_code: str | None = None,
        updated_at: str,
    ) -> None:
        assignments = ["processing_status = ?", "updated_at = ?"]
        params: list[Any] = [processing_status, updated_at]
        if processing_status == "succeeded":
            assignments.append("error_code = null")
            error_code = None
        for column, value in (
            ("disposition", disposition),
            ("content_identity_hash", content_identity_hash),
            ("card_id", card_id),
            ("error_code", error_code),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                params.append(value)
        params.append(capture_id)
        with self.connect() as conn:
            conn.execute(
                f"update capture_receipts set {', '.join(assignments)} where capture_id = ?",
                tuple(params),
            )

    def _one(
        self,
        sql: str,
        params: tuple[Any, ...],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> CaptureReceiptRow | CapturedCardRow | CaptureCountRow | None:
        if connection is not None:
            row = connection.execute(sql, params).fetchone()
            return _receipt_row(row) if row else None
        with self.connect_existing() as conn:
            row = conn.execute(sql, params).fetchone()
        return _receipt_row(row) if row else None


def _receipt_row(row: sqlite3.Row) -> CaptureReceiptRow | CapturedCardRow | CaptureCountRow:
    return cast(CaptureReceiptRow | CapturedCardRow | CaptureCountRow, dict(row))
