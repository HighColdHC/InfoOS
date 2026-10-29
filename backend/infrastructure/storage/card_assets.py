"""Card asset repository.

card_assets — module migrated to infoos in PR 4.10.
The corresponding infoos/storage/card_assets.py was deleted in this commit.
This file is now the sole source of truth.
"""

from __future__ import annotations

import sqlite3
from typing import Any, cast

from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now


from backend.infrastructure.storage.card_assets_types import CardAssetRow


class CardAssetRepository(InfoOSRepository):
    def upsert_asset(self, payload: dict[str, Any], *, connection: sqlite3.Connection | None = None) -> None:
        if connection is None:
            with self.connect() as conn:
                self.upsert_asset(payload, connection=conn)
            return
        now = utc_now()
        data = {
            "asset_id": payload["asset_id"],
            "card_id": payload["card_id"],
            "asset_kind": payload["asset_kind"],
            "source_url": payload["source_url"],
            "remote_url": payload.get("remote_url"),
            "local_path": payload.get("local_path"),
            "mime_type": payload.get("mime_type"),
            "size_bytes": payload.get("size_bytes"),
            "content_hash": payload.get("content_hash"),
            "preservation_class": payload.get("preservation_class", "cached"),
            "status": payload["status"],
            "cleanup_state": payload.get("cleanup_state", "present"),
            "raw_payload": dumps(payload.get("raw_payload", {})),
            "created_at": payload.get("created_at", now),
            "updated_at": now,
        }
        if connection is not None:
            connection.execute(
                """
                insert into card_assets (
                  asset_id, card_id, asset_kind, source_url, remote_url, local_path, mime_type,
                  size_bytes, content_hash, preservation_class, status, cleanup_state, raw_payload, created_at, updated_at
                ) values (
                  :asset_id, :card_id, :asset_kind, :source_url, :remote_url, :local_path, :mime_type,
                  :size_bytes, :content_hash, :preservation_class, :status, :cleanup_state, :raw_payload, :created_at, :updated_at
                )
                on conflict(asset_id) do update set
                  card_id=excluded.card_id,
                  asset_kind=excluded.asset_kind,
                  source_url=excluded.source_url,
                  remote_url=excluded.remote_url,
                  local_path=excluded.local_path,
                  mime_type=excluded.mime_type,
                  size_bytes=excluded.size_bytes,
                  content_hash=excluded.content_hash,
                  preservation_class=excluded.preservation_class,
                  status=excluded.status,
                  cleanup_state=excluded.cleanup_state,
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
    ) -> list[CardAssetRow]:
        if connection is not None:
            rows = connection.execute(
                """
                select *
                from card_assets
                where card_id = ?
                order by created_at, asset_id
                """,
                (card_id,),
            ).fetchall()
        else:
            with self.connect_existing() as conn:
                rows = conn.execute(
                    """
                    select *
                    from card_assets
                    where card_id = ?
                    order by created_at, asset_id
                    """,
                    (card_id,),
                ).fetchall()
        return [self._decode(dict(row)) for row in rows]

    def get_asset(self, asset_id: str) -> CardAssetRow | None:
        with self.connect_existing() as conn:
            row = conn.execute("select * from card_assets where asset_id = ?", (asset_id,)).fetchone()
        return self._decode(dict(row)) if row else None

    def list_x_cards_with_hash(self, content_hash: str, *, exclude_card_id: str) -> list[dict[str, Any]]:
        """Find prior X cards sharing one exact asset hash for attribution checks."""
        with self.connect_existing() as conn:
            rows = conn.execute(
                """
                select distinct c.card_id, c.source_url
                from card_assets as a join cards as c on c.card_id = a.card_id
                where a.content_hash = ? and a.card_id != ? and c.source_platform = 'x'
                order by c.card_id
                """,
                (content_hash, exclude_card_id),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _decode(payload: dict[str, Any]) -> CardAssetRow:
        return cast(CardAssetRow, payload | {"raw_payload": loads(payload["raw_payload"], {})})
