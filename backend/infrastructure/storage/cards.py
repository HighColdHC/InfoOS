"""Card repository.

cards — module migrated to infoos in PR 4.12.
The corresponding infoos/storage/cards.py was deleted in this commit.
This file is now the sole source of truth.
"""

from __future__ import annotations

import sqlite3
from typing import Any, cast

from backend.domain.timestamps import capture_timestamp
from backend.infrastructure.storage.base import (
    InfoOSRepository,
    card_quality_payload,
    dumps,
    loads,
    utc_now,
)
from backend.infrastructure.storage.card_read_keys import card_read_keys
from backend.infrastructure.storage.cards_types import CardRow


_CARD_FIELDS = """
  card_id, card_type, version, content_hash, updated_at, status,
  completeness_status, missing_reasons, processor_version, raw_connector_id,
  raw_item_id, source_run_id, source_id, source_platform, source_type,
  source_url, source_author, title, content, summary, primary_domain_id,
  secondary_domain_ids, published_at, captured_at, processed_at,
  workflow_status, ignored, skimmed, raw_payload
"""

_CARD_COLUMNS = _CARD_FIELDS + """,
  (select r.first_seen_at from raw_items r
   where r.connector_id=cards.raw_connector_id and r.raw_item_id=cards.raw_item_id) as first_seen_at
"""


class CardRepository(InfoOSRepository):
    def upsert_card(self, payload: dict[str, Any]) -> None:
        updated_at = payload.get("updated_at") or payload.get("processed_at") or utc_now()
        data = {
            "card_id": payload["card_id"],
            "card_type": payload.get("card_type", "information"),
            "version": int(payload.get("version", 0)),
            "content_hash": payload.get("content_hash"),
            "updated_at": updated_at,
            "status": payload.get("status", "processing"),
            "completeness_status": payload.get("completeness_status", "processing"),
            "missing_reasons": dumps(payload.get("missing_reasons", [])),
            "processor_version": payload.get("processor_version"),
            "raw_connector_id": payload.get("raw_connector_id"),
            "raw_item_id": payload.get("raw_item_id"),
            "source_run_id": payload.get("source_run_id"),
            "source_id": payload.get("source_id"),
            "source_platform": payload["source_platform"],
            "source_type": payload["source_type"],
            "source_url": payload["source_url"],
            "source_author": payload.get("source_author"),
            "title": payload["title"],
            "content": payload["content"],
            "summary": payload.get("summary", ""),
            "primary_domain_id": payload.get("primary_domain_id"),
            "secondary_domain_ids": dumps(payload.get("secondary_domain_ids", [])),
            "published_at": payload.get("published_at"),
            "captured_at": capture_timestamp(payload["captured_at"], payload.get("processed_at")),
            "processed_at": payload["processed_at"],
            "workflow_status": payload.get("workflow_status", "new"),
            "ignored": 1 if payload.get("ignored", False) else 0,
            "skimmed": 1 if payload.get("skimmed", False) else 0,
            "raw_payload": dumps(payload.get("raw_payload", {})),
        }
        with self.connect() as conn:
            conn.execute("begin immediate")
            raw = conn.execute(
                "select first_seen_at from raw_items where connector_id=? and raw_item_id=?",
                (data["raw_connector_id"], data["raw_item_id"]),
            ).fetchone()
            data["sort_ts"], data["published_ts"] = card_read_keys(data["published_at"], raw[0] if raw else None)
            conn.execute(
                """
                insert into cards (
                  card_id, card_type, version, content_hash, updated_at, status,
                  completeness_status, missing_reasons, processor_version, raw_connector_id,
                  raw_item_id, source_run_id, source_id, source_platform, source_type,
                  source_url, source_author, title, content, summary, primary_domain_id,
                  secondary_domain_ids, published_at, captured_at, processed_at,
                  workflow_status, ignored, skimmed, raw_payload, sort_ts, published_ts
                ) values (
                  :card_id, :card_type, :version, :content_hash, :updated_at, :status,
                  :completeness_status, :missing_reasons, :processor_version, :raw_connector_id,
                  :raw_item_id, :source_run_id, :source_id, :source_platform, :source_type,
                  :source_url, :source_author, :title, :content, :summary, :primary_domain_id,
                  :secondary_domain_ids, :published_at, :captured_at, :processed_at,
                  :workflow_status, :ignored, :skimmed, :raw_payload, :sort_ts, :published_ts
                )
                on conflict(card_id) do update set
                  card_type=excluded.card_type,
                  status=excluded.status,
                  completeness_status=excluded.completeness_status,
                  missing_reasons=excluded.missing_reasons,
                  processor_version=excluded.processor_version,
                  raw_connector_id=excluded.raw_connector_id,
                  raw_item_id=excluded.raw_item_id,
                  source_run_id=excluded.source_run_id,
                  source_id=excluded.source_id,
                  source_platform=excluded.source_platform,
                  source_type=excluded.source_type,
                  source_url=excluded.source_url,
                  source_author=excluded.source_author,
                  title=excluded.title,
                  content=excluded.content,
                  summary=excluded.summary,
                  primary_domain_id=excluded.primary_domain_id,
                  secondary_domain_ids=excluded.secondary_domain_ids,
                  published_at=excluded.published_at,
                  sort_ts=excluded.sort_ts,
                  published_ts=excluded.published_ts,
                  captured_at=excluded.captured_at,
                  processed_at=excluded.processed_at,
                  workflow_status=excluded.workflow_status,
                  raw_payload=excluded.raw_payload
                """,
                data,
            )

    def finalize_card(
        self,
        card_id: str,
        *,
        content_hash: str,
        completeness_status: str,
        missing_reasons: list[Any],
        processor_version: str | None = None,
        source_run_id: str | None = None,
        updated_at: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Finalize one card inside either an owned or caller-owned transaction."""

        if completeness_status not in {"complete", "partial"}:
            raise ValueError("completeness_status must be complete or partial")
        if not content_hash.startswith("sha256:"):
            raise ValueError("content_hash must use the sha256: prefix")
        if conn is not None:
            return self._finalize_in_connection(
                conn,
                card_id=card_id,
                content_hash=content_hash,
                completeness_status=completeness_status,
                missing_reasons=missing_reasons,
                processor_version=processor_version,
                source_run_id=source_run_id,
                updated_at=updated_at,
            )
        with self.connect() as own_conn:
            return self._finalize_in_connection(
                own_conn,
                card_id=card_id,
                content_hash=content_hash,
                completeness_status=completeness_status,
                missing_reasons=missing_reasons,
                processor_version=processor_version,
                source_run_id=source_run_id,
                updated_at=updated_at,
            )

    @staticmethod
    def _finalize_in_connection(
        conn: sqlite3.Connection,
        *,
        card_id: str,
        content_hash: str,
        completeness_status: str,
        missing_reasons: list[Any],
        processor_version: str | None,
        source_run_id: str | None,
        updated_at: str | None,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            select card_id, version, content_hash, updated_at, status,
              completeness_status, missing_reasons, processor_version, source_run_id
            from cards where card_id = ?
            """,
            (card_id,),
        ).fetchone()
        if row is None:
            raise KeyError(card_id)

        current = dict(row)
        changed = current.get("content_hash") != content_hash
        if changed:
            next_version = current["version"] + 1
            next_updated_at = updated_at or utc_now()
            conn.execute(
                """
                update cards set
                  version = ?, content_hash = ?, updated_at = ?, status = 'active',
                  completeness_status = ?, missing_reasons = ?, processor_version = ?,
                  source_run_id = ?
                where card_id = ?
                """,
                (
                    next_version,
                    content_hash,
                    next_updated_at,
                    completeness_status,
                    dumps(missing_reasons),
                    processor_version,
                    source_run_id,
                    card_id,
                ),
            )
        else:
            next_version = current["version"]
            next_updated_at = current["updated_at"]
            conn.execute(
                """
                update cards set status = 'active', completeness_status = ?,
                  missing_reasons = ?, processor_version = ?, source_run_id = ?
                where card_id = ?
                """,
                (
                    completeness_status,
                    dumps(missing_reasons),
                    processor_version,
                    source_run_id,
                    card_id,
                ),
            )

        return {
            "card_id": card_id,
            "version": next_version,
            "content_hash": content_hash,
            "updated_at": next_updated_at,
            "status": "active",
            "completeness_status": completeness_status,
            "missing_reasons": list(missing_reasons),
            "processor_version": processor_version,
            "source_run_id": source_run_id,
            "changed": changed,
        }

    def list_reconciliation_page(
        self,
        *,
        after_card_id: str | None,
        limit: int,
    ) -> list[CardRow]:
        """Return a bounded, wrapping keyset page so old cards cannot starve."""
        page_size = max(0, int(limit))
        if page_size == 0:
            return []
        cursor = str(after_card_id or "")
        with self.connect_existing() as conn:
            rows = conn.execute(
                f"select {_CARD_COLUMNS} from cards where card_id > ? order by card_id limit ?",
                (cursor, page_size),
            ).fetchall()
            if len(rows) < page_size and cursor:
                rows.extend(
                    conn.execute(
                        f"select {_CARD_COLUMNS} from cards where card_id <= ? order by card_id limit ?",
                        (cursor, page_size - len(rows)),
                    ).fetchall()
                )
        return [self._decode(dict(row)) for row in rows]

    def list_terminal_state_repair_candidates(
        self,
        *,
        limit: int = 25,
    ) -> list[CardRow]:
        """Prioritize cards whose successful finalize was later overwritten."""
        page_size = max(0, min(int(limit), 100))
        if page_size == 0:
            return []
        with self.connect_existing() as conn:
            rows = conn.execute(
                f"""
                select {_CARD_COLUMNS} from cards
                where status = 'processing'
                  and content_hash like 'sha256:%'
                  and exists (
                    select 1 from tasks
                    where task_type = 'card_finalize'
                      and target_kind = 'card'
                      and target_id = cards.card_id
                      and status = 'success'
                      and json_extract(
                        artifacts_summary, '$.content_hash'
                      ) = cards.content_hash
                  )
                order by updated_at, card_id
                limit ?
                """,
                (page_size,),
            ).fetchall()
        return [self._decode(dict(row)) for row in rows]

    def get_card_platform(self, card_id: str) -> str | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                "select source_platform from cards where card_id = ?", (card_id,)
            ).fetchone()
        return row["source_platform"] if row is not None else None

    def get_card(
        self,
        card_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> CardRow | None:
        if connection is not None:
            row = connection.execute(
                f"select {_CARD_COLUMNS} from cards where card_id = ?", (card_id,)
            ).fetchone()
        else:
            with self.connect_existing() as conn:
                row = conn.execute(
                    f"select {_CARD_COLUMNS} from cards where card_id = ?",
                    (card_id,),
                ).fetchone()
        if row is None:
            return None
        return self._decode(dict(row))

    @staticmethod
    def _decode(card: dict[str, Any]) -> CardRow:
        card = card | {"ignored": bool(card["ignored"]), "skimmed": bool(card["skimmed"])}
        raw_payload = loads(card["raw_payload"], {})
        quality_payload = card_quality_payload(raw_payload)
        card["raw_payload"] = raw_payload
        card["missing_reasons"] = loads(card["missing_reasons"], [])
        card["secondary_domain_ids"] = loads(card["secondary_domain_ids"], [])
        card["content_quality"] = quality_payload.get("content_quality")
        card["content_needs_enrichment"] = bool(quality_payload.get("content_needs_enrichment", False))
        return cast(CardRow, card)
