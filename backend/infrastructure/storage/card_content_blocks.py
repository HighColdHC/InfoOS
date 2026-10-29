"""Card content block repository.

card_content_blocks — module migrated to infoos in PR 4.11.
The corresponding infoos/storage/card_content_blocks.py was deleted in this commit.
This file is now the sole source of truth.
"""

from __future__ import annotations

import sqlite3
from typing import Any, cast

from backend.domain.content_blocks import normalize_content_block_kind
from backend.infrastructure.security.redaction import summarize_subprocess_output
from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now


from backend.infrastructure.storage.card_content_blocks_types import CardContentBlockRow


class CardContentBlockRepository(InfoOSRepository):
    def upsert_block(
        self,
        payload: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if connection is not None:
            self._upsert_to_connection(connection, payload)
            return
        with self.connect() as conn:
            self._upsert_to_connection(conn, payload)

    def upsert_blocks(
        self,
        payloads: list[dict[str, Any]],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if connection is not None:
            for payload in payloads:
                self._upsert_to_connection(connection, payload)
            return
        with self.connect() as conn:
            for payload in payloads:
                self._upsert_to_connection(conn, payload)

    @staticmethod
    def _upsert_to_connection(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
        now = utc_now()
        data = {
            "block_id": payload["block_id"],
            "card_id": payload["card_id"],
            "position": int(payload.get("position", 0)),
            "layer": payload["layer"],
            "block_kind": normalize_content_block_kind(payload.get("block_kind")),
            "status": payload["status"],
            "body": payload.get("body", ""),
            "segments": dumps(payload.get("segments", [])),
            "source_url": payload.get("source_url"),
            "provenance": payload.get("provenance", ""),
            "raw_payload": dumps(_safe_media_gap_payload(payload)),
            "created_at": payload.get("created_at", now),
            "updated_at": now,
        }
        conn.execute(
                """
                insert into card_content_blocks (
                  block_id, card_id, position, layer, block_kind, status, body, segments,
                  source_url, provenance, raw_payload, created_at, updated_at
                ) values (
                  :block_id, :card_id, :position, :layer, :block_kind, :status, :body, :segments,
                  :source_url, :provenance, :raw_payload, :created_at, :updated_at
                )
                on conflict(block_id) do update set
                  card_id=excluded.card_id,
                  position=excluded.position,
                  layer=excluded.layer,
                  block_kind=excluded.block_kind,
                  status=excluded.status,
                  body=excluded.body,
                  segments=excluded.segments,
                  source_url=excluded.source_url,
                  provenance=excluded.provenance,
                  raw_payload=excluded.raw_payload,
                  updated_at=excluded.updated_at
                """,
            data,
        )

    def list_for_card(
        self,
        card_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[CardContentBlockRow]:
        if connection is not None:
            rows = connection.execute(
                """
                select *
                from card_content_blocks
                where card_id = ?
                order by position, created_at, block_id
                """,
                (card_id,),
            ).fetchall()
        else:
            with self.connect_existing() as conn:
                rows = conn.execute(
                    """
                    select *
                    from card_content_blocks
                    where card_id = ?
                    order by position, created_at, block_id
                    """,
                    (card_id,),
                ).fetchall()
        return [self._decode(dict(row)) for row in rows]

    def delete_blocks(
        self,
        block_ids: list[str],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """Delete an explicit, already-resolved set of block identities."""
        resolved = sorted({str(block_id).strip() for block_id in block_ids if str(block_id).strip()})
        if not resolved:
            return 0
        if connection is not None:
            return self._delete_from_connection(connection, resolved)
        with self.connect() as conn:
            return self._delete_from_connection(conn, resolved)

    @staticmethod
    def _delete_from_connection(conn: sqlite3.Connection, block_ids: list[str]) -> int:
        placeholders = ",".join("?" for _ in block_ids)
        cursor = conn.execute(
            f"delete from card_content_blocks where block_id in ({placeholders})",
            block_ids,
        )
        return int(cursor.rowcount)

    @staticmethod
    def _decode(payload: dict[str, Any]) -> CardContentBlockRow:
        return cast(CardContentBlockRow, payload | {
            "segments": loads(payload["segments"], []),
            "raw_payload": loads(payload["raw_payload"], {}),
        })


def _safe_media_gap_payload(block: dict[str, Any]) -> dict[str, Any]:
    raw_payload = block.get("raw_payload")
    if not isinstance(raw_payload, dict):
        return {}
    details = raw_payload.get("details")
    if block.get("block_kind") != "media_download_gap" or not isinstance(details, dict):
        return raw_payload
    if not ({"stderr", "stdout"} & details.keys()):
        return raw_payload
    summary = summarize_subprocess_output(details.get("stdout"), details.get("stderr"))
    safe_details = {key: value for key, value in details.items() if key not in {"stderr", "stdout"}}
    if summary:
        safe_details["stderr_summary"] = summary
    return {**raw_payload, "details": safe_details}
