"""Versioned interest-profile persistence."""
from __future__ import annotations

import sqlite3
from typing import NotRequired, TypedDict, cast

from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now


class InterestProfileRow(TypedDict):
    profile_version: int
    markdown: str
    content_hash: str
    source: str
    created_at: str
    sections: object


class InterestProfileQueryRow(TypedDict):
    profile_version: int
    markdown: str
    content_hash: str
    source: str
    created_at: str
    sections_json: NotRequired[str]
    sections: NotRequired[object]


_COLUMNS = "profile_version, markdown, sections_json, content_hash, source, created_at"


class InterestProfileRepository(InfoOSRepository):
    def get(self, version: int | None = None, *, connection: sqlite3.Connection | None = None) -> InterestProfileRow | None:
        if connection is None:
            with self.connect_existing() as conn:
                return self.get(version, connection=conn)
        conn = connection
        if version is None:
            row = conn.execute(
                f"select {_COLUMNS} from interest_profiles order by profile_version desc limit 1"
            ).fetchone()
        else:
            row = conn.execute(
                f"select {_COLUMNS} from interest_profiles where profile_version = ?", (version,)
            ).fetchone()
        return self._decode(row)

    @staticmethod
    def insert_current(
        conn: sqlite3.Connection, *, markdown: str, sections: dict[str, list[str]],
        content_hash: str, source: str, created_at: str | None = None,
    ) -> tuple[InterestProfileRow, bool]:
        row = conn.execute(
            f"select {_COLUMNS} from interest_profiles order by profile_version desc limit 1"
        ).fetchone()
        if row is not None and row["content_hash"] == content_hash:
            return InterestProfileRepository._decode(row), False
        version = int(row["profile_version"]) + 1 if row is not None else 1
        timestamp = normalize_timestamp(created_at or utc_now())
        if timestamp is None:
            raise ValueError("created_at must be a valid timestamp")
        conn.execute(
            """insert into interest_profiles
               (profile_version, markdown, sections_json, content_hash, source, created_at)
               values (?, ?, ?, ?, ?, ?)""",
            (version, markdown, dumps(sections), content_hash, source, timestamp),
        )
        result = conn.execute(
            f"select {_COLUMNS} from interest_profiles where profile_version = ?", (version,)
        ).fetchone()
        return InterestProfileRepository._decode(result), True

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> InterestProfileRow | None:
        if row is None:
            return None
        result = cast(InterestProfileQueryRow, dict(row))
        result["sections"] = loads(result.pop("sections_json"), {})
        return cast(InterestProfileRow, result)
