"""Persist derived thumbnail state independently of CardAssets."""
from __future__ import annotations

from typing import cast

from backend.infrastructure.storage import base
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.card_asset_thumbnails_types import (
    CardAssetThumbnailRow, ThumbnailSourceRow, ThumbnailSummary,
)

_ID_BATCH = 500
_PENDING_SQL = """
select a.asset_id, a.card_id, a.local_path, a.content_hash,
       case when lower(a.asset_kind) = 'image' or lower(coalesce(a.mime_type, '')) like 'image/%'
            then 'image' else 'video' end as media_kind
from card_assets a
left join card_asset_thumbnails t on t.asset_id = a.asset_id
where a.cleanup_state = 'present'
  and a.status in ('done', 'downloaded', 'enriched', 'preserved', 'ready', 'understood')
  and coalesce(a.local_path, '') != ''
  and (lower(a.asset_kind) in ('image', 'video')
       or lower(coalesce(a.mime_type, '')) like 'image/%'
       or lower(coalesce(a.mime_type, '')) like 'video/%')
  and (t.asset_id is null
       or t.source_content_hash is not a.content_hash
       or (? and t.status = 'failed'))
"""
_UPSERT_SQL = """
insert into card_asset_thumbnails (
    asset_id, status, local_path, width, height, size_bytes, source_content_hash,
    error_code, created_at, updated_at
) values (:asset_id, :status, :local_path, :width, :height, :size_bytes,
          :source_content_hash, :error_code, :created_at, :updated_at)
on conflict(asset_id) do update set
    status=excluded.status, local_path=excluded.local_path, width=excluded.width,
    height=excluded.height, size_bytes=excluded.size_bytes,
    source_content_hash=excluded.source_content_hash, error_code=excluded.error_code,
    updated_at=excluded.updated_at
"""


class CardAssetThumbnailRepository(InfoOSRepository):
    def list_pending_for_card(self, card_id: str) -> list[ThumbnailSourceRow]:
        with self.connect_existing() as conn:
            rows = conn.execute(_PENDING_SQL + " and a.card_id = ?", (0, card_id)).fetchall()
        return [cast(ThumbnailSourceRow, dict(row)) for row in rows]

    def list_pending_batch(self, *, after_asset_id: str, limit: int, retry_failed: bool) -> list[ThumbnailSourceRow]:
        with self.connect_existing() as conn:
            rows = conn.execute(
                _PENDING_SQL + " and a.asset_id > ? order by a.asset_id limit ?",
                (retry_failed, after_asset_id, limit),
            ).fetchall()
        return [cast(ThumbnailSourceRow, dict(row)) for row in rows]

    def summary(self, *, retry_failed: bool) -> ThumbnailSummary:
        result: ThumbnailSummary = {"pending_image": 0, "pending_video": 0, "ready": 0, "failed": 0, "ready_bytes": 0}
        with self.connect_existing() as conn:
            for row in conn.execute(
                "select media_kind, count(*) from (" + _PENDING_SQL + ") group by media_kind", (retry_failed,),
            ):
                if row[0] == "image":
                    result["pending_image"] = row[1]
                else:
                    result["pending_video"] = row[1]
            for status, count, size_bytes in conn.execute(
                "select status, count(*), coalesce(sum(size_bytes), 0) from card_asset_thumbnails group by status"
            ):
                if status == "ready":
                    result["ready"] = count
                    result["ready_bytes"] = size_bytes
                else:
                    result["failed"] = count
        return result

    def get(self, asset_id: str) -> CardAssetThumbnailRow | None:
        with self.connect_existing() as conn:
            row = conn.execute("select * from card_asset_thumbnails where asset_id = ?", (asset_id,)).fetchone()
        return cast(CardAssetThumbnailRow, dict(row)) if row else None

    def states_for(self, asset_ids: list[str]) -> dict[str, CardAssetThumbnailRow]:
        result = {}
        with self.connect_existing() as conn:
            for start in range(0, len(asset_ids), _ID_BATCH):
                batch = asset_ids[start:start + _ID_BATCH]
                placeholders = ",".join("?" for _ in batch)
                for row in conn.execute(f"select * from card_asset_thumbnails where asset_id in ({placeholders})", batch):
                    state = cast(CardAssetThumbnailRow, dict(row))
                    result[state["asset_id"]] = state
        return result

    def mark_ready(self, asset_id: str, *, local_path: str, width: int, height: int,
                   size_bytes: int, source_content_hash: str | None) -> None:
        now = base.utc_now()
        with self.connect() as conn:
            conn.execute(_UPSERT_SQL, {
                "asset_id": asset_id, "status": "ready", "local_path": local_path,
                "width": width, "height": height, "size_bytes": size_bytes,
                "source_content_hash": source_content_hash, "error_code": None,
                "created_at": now, "updated_at": now,
            })

    def mark_failed(self, asset_id: str, *, error_code: str, source_content_hash: str | None) -> None:
        now = base.utc_now()
        with self.connect() as conn:
            conn.execute(_UPSERT_SQL, {
                "asset_id": asset_id, "status": "failed", "local_path": None,
                "width": None, "height": None, "size_bytes": None,
                "source_content_hash": source_content_hash, "error_code": error_code,
                "created_at": now, "updated_at": now,
            })
