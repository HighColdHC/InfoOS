"""Idempotent card evaluations and their read paths."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, TypedDict, cast

from backend.domain.timestamps import normalize_timestamp, parse_utc
from backend.infrastructure.storage.base import InfoOSRepository, utc_now


_COLUMNS = """evaluation_id, card_id, card_version, profile_version, prompt_version,
model, provider_id, quality, relevance, attribution_kind, attribution_ref,
content_type, suggested_action, paused_landmark, summary, relation_note,
passed_quality, receipt_id, created_at, writeback_at, exclusion, extract_kinds"""


class CardEvaluationRow(TypedDict):
    evaluation_id: str
    card_id: str
    card_version: int
    profile_version: int
    prompt_version: str
    model: str
    provider_id: str
    quality: int
    relevance: int | None
    attribution_kind: str | None
    attribution_ref: str | None
    content_type: str
    suggested_action: str
    paused_landmark: int
    summary: str
    relation_note: str
    passed_quality: int
    receipt_id: str | None
    created_at: str
    writeback_at: str | None
    exclusion: str | None
    extract_kinds: list[str] | None


class CardEvaluationRepository(InfoOSRepository):
    @staticmethod
    def insert(conn: sqlite3.Connection, payload: dict[str, Any], *, overwrite: bool = False) -> bool:
        """Write in the caller transaction; created_at is the key's first write."""
        timestamp = normalize_timestamp(payload.get("created_at") or utc_now())
        if timestamp is None:
            raise ValueError("created_at must be a valid timestamp")
        # created_at is UTC with fixed six-digit microseconds and Z for lexical ordering.
        timestamp = parse_utc(timestamp).isoformat(timespec="microseconds").replace("+00:00", "Z")
        values = {**payload, "created_at": timestamp, "writeback_at": timestamp}
        values["exclusion"] = payload.get("exclusion")
        values["extract_kinds"] = json.dumps(payload["extract_kinds"]) if payload.get("extract_kinds") is not None else None
        values["paused_landmark"] = int(bool(values["paused_landmark"]))
        values["passed_quality"] = int(bool(values["passed_quality"]))
        conflict = "do update set " + ", ".join(
            f"{field}=excluded.{field}" for field in (name.strip() for name in _COLUMNS.split(","))
            if field not in {"evaluation_id", "card_id", "card_version", "profile_version", "prompt_version", "created_at"}
        ) if overwrite else "do nothing"
        cursor = conn.execute(
            f"""insert into card_evaluations (
              evaluation_id, card_id, card_version, profile_version, prompt_version,
              model, provider_id, quality, relevance, attribution_kind, attribution_ref,
              content_type, suggested_action, paused_landmark, summary, relation_note,
              passed_quality, receipt_id, created_at, writeback_at, exclusion, extract_kinds
            ) values (
              :evaluation_id, :card_id, :card_version, :profile_version, :prompt_version,
              :model, :provider_id, :quality, :relevance, :attribution_kind, :attribution_ref,
              :content_type, :suggested_action, :paused_landmark, :summary, :relation_note,
              :passed_quality, :receipt_id, :created_at, :writeback_at, :exclusion, :extract_kinds
            ) on conflict(card_id, card_version, profile_version, prompt_version) {conflict}""",
            values,
        )
        return cursor.rowcount == 1

    def latest_for_card(self, card_id: str) -> CardEvaluationRow | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                f"select {_COLUMNS} from card_evaluations where card_id = ? "
                "order by created_at desc, evaluation_id desc limit 1", (card_id,),
            ).fetchone()
        return _decode(row) if row is not None else None

    def in_window(self, start: str, end: str, *, limit: int = 100) -> list[CardEvaluationRow]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.connect_existing() as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from card_evaluations "
                "where created_at >= ? and created_at < ? and passed_quality = 1 "
                "order by relevance desc, created_at desc, evaluation_id desc limit ?",
                (start, end, limit),
            ).fetchall()
        return [_decode(row) for row in rows]


def _decode(row: sqlite3.Row) -> CardEvaluationRow:
    value = dict(row)
    value["extract_kinds"] = json.loads(value["extract_kinds"]) if value["extract_kinds"] is not None else None
    return cast(CardEvaluationRow, value)
