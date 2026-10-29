"""Storage queries used by research batch creation and projections."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, TypedDict, cast

from backend.domain.json_types import JsonObject
from backend.infrastructure.storage.task_batch_queries import BatchRow


class ResearchBatchIdentityRow(TypedDict):
    batch_id: str
    metadata: str


class ResearchBatchIdentity(TypedDict):
    batch_id: str
    metadata: JsonObject


def count_token_research_runs(
    conn: sqlite3.Connection, token_id: str, *, since: str, until: str,
) -> int:
    return int(conn.execute(
        """select count(*) from task_batches
           where batch_type = 'research'
             and json_extract(metadata, '$.origin.token_id') = ?
             and created_at > ? and created_at <= ?""",
        (token_id, since, until),
    ).fetchone()[0])


def find_research_batch_by_idempotency_hash(
    conn: sqlite3.Connection,
    key_hash: str,
) -> ResearchBatchIdentity | None:
    rows = conn.execute(
        """
        select batch_id, metadata from task_batches
        where batch_type = 'research'
        order by created_at desc
        """
    ).fetchall()
    for raw_row in rows:
        row = cast(ResearchBatchIdentityRow, dict(raw_row))
        metadata = _json_object(row["metadata"])
        if metadata.get("idempotency_key_hash") == key_hash:
            return {
                "batch_id": str(row["batch_id"]),
                "metadata": metadata,
            }
    return None


def research_batch_rows(
    conn: sqlite3.Connection,
    *,
    limit: int,
) -> list[BatchRow]:
    return [cast(BatchRow, dict(row)) for row in conn.execute(
        """
        select * from task_batches
        where batch_type = 'research'
        order by created_at desc, batch_id desc
        limit ?
        """,
        (max(1, min(int(limit), 100)),),
    ).fetchall()]


def _json_object(value: Any) -> JsonObject:
    try:
        loaded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
