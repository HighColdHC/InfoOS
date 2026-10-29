"""Append-only source change evidence; deliberately survives source/token deletion."""
from datetime import datetime, timezone
import sqlite3
from typing import Any, TypedDict, cast
from uuid import uuid4

from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.source_config_versions import canonical_config_version


class SourceChangeRow(TypedDict):
    audit_id: str
    source_id: str
    action: str
    actor_kind: str
    token_id: str | None
    reason: str | None
    before_config_version: str | None
    after_config_version: str | None
    created_at: str


class SourceChangePage(TypedDict):
    items: list[SourceChangeRow]
    total: int
    limit: int
    offset: int


def append_source_change(conn: sqlite3.Connection, *, source_id: str, action: str,
                         before: dict[str, Any] | None, after: dict[str, Any],
                         actor_kind: str = "web", token_id: str | None = None,
                         reason: str | None = None) -> None:
    conn.execute(
        """insert into source_change_audit
           (audit_id, source_id, action, actor_kind, token_id, reason,
            before_config_version, after_config_version, created_at)
           values (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid4()), source_id, action, actor_kind, token_id, reason,
         canonical_config_version(before) if before is not None else None,
         canonical_config_version(after),
         datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")),
    )


class SourceChangeAuditRepository(InfoOSRepository):
    def page(self, source_id: str | None = None, *, limit: int = 50, offset: int = 0) -> SourceChangePage:
        where = " where source_id = ?" if source_id is not None else ""
        params = (source_id,) if source_id is not None else ()
        with self.connect_existing() as conn:
            total = conn.execute("select count(*) from source_change_audit" + where, params).fetchone()[0]
            rows = conn.execute(
                "select * from source_change_audit" + where +
                " order by created_at desc, rowid desc limit ? offset ?", (*params, limit, offset),
            ).fetchall()
        return {"items": [cast(SourceChangeRow, dict(row)) for row in rows], "total": total, "limit": limit, "offset": offset}
