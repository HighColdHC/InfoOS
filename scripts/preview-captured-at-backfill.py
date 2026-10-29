#!/usr/bin/env python3
"""Read-only preview for repairing legacy captured_at values.

Usage: python scripts/preview-captured-at-backfill.py /path/to/infoos.sqlite3
The connection is opened mode=ro with query_only=ON. No migration is executed.

A row needs repair when captured_at is not canonical UTC ISO (``...Z``) or when it
names the same instant as published_at: legacy connectors copied the publisher
date into captured_at, so a well-formed copy is still not an acquisition time.
The script is standalone so it can be piped into the appliance container.
"""

from __future__ import annotations

import argparse
import email.utils
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def normalized_utc(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def publish_instant(value: str | None) -> str | None:
    """Mirror backend.domain.timestamps.normalize_timestamp (standalone copy)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{10,16}(?:\.\d+)?", text):
            seconds = float(text)
            if seconds >= 1e14:
                seconds /= 1_000_000
            elif seconds >= 1e11:
                seconds /= 1_000
            parsed = datetime.fromtimestamp(seconds, timezone.utc)
        else:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                parsed = email.utils.parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def repair_reason(captured: str | None, published: str | None) -> str | None:
    if not (captured and UTC_ISO.fullmatch(captured) and normalized_utc(captured) == captured):
        return "non_canonical"
    if published and captured == published:
        return "publish_time_copy"
    pub = publish_instant(published)
    if pub and publish_instant(captured) == pub:
        return "publish_time_copy"
    return None


def _cards(conn: sqlite3.Connection, sample_limit: int) -> dict:
    rows = conn.execute(
        """
        select c.card_id, c.source_platform, c.captured_at, c.published_at,
               r.first_seen_at as raw_first_seen_at,
               coalesce(h.started_at, raw_h.started_at) as source_run_started_at
        from cards c
        left join raw_items r
          on r.connector_id = c.raw_connector_id and r.raw_item_id = c.raw_item_id
        left join run_history h on h.run_id = c.source_run_id
        left join run_history raw_h on raw_h.run_id = r.run_id
        order by c.card_id
        """
    )
    by_platform: Counter[str] = Counter()
    planned_by_platform: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    evidence: Counter[str] = Counter()
    unresolved: list[dict] = []
    samples: list[dict] = []
    sampled_platforms: Counter[str] = Counter()
    total = invalid = planned = 0
    for row in rows:
        total += 1
        before, platform = row["captured_at"], row["source_platform"]
        reason = repair_reason(before, row["published_at"])
        if reason is None:
            continue
        invalid += 1
        by_platform[platform] += 1
        reasons[reason] += 1
        raw_time = normalized_utc(row["raw_first_seen_at"])
        run_time = normalized_utc(row["source_run_started_at"])
        after = raw_time or run_time
        source = "raw_items.first_seen_at" if raw_time else "run_history.started_at" if run_time else None
        if after is None:
            unresolved.append({"card_id": row["card_id"], "platform": platform, "before": before})
        else:
            planned += 1
            planned_by_platform[platform] += 1
            evidence[source] += 1
        # Spread samples across platforms so every platform is reviewable.
        if len(samples) < sample_limit and sampled_platforms[platform] < 2:
            sampled_platforms[platform] += 1
            samples.append({
                "card_id": row["card_id"], "platform": platform, "reason": reason,
                "before": before, "published_at": row["published_at"],
                "after": after, "evidence": source,
            })
    return {
        "total_cards": total, "needs_repair_cards": invalid, "would_update_cards": planned,
        "reasons": dict(sorted(reasons.items())),
        "by_platform": dict(sorted(by_platform.items())),
        "would_update_by_platform": dict(sorted(planned_by_platform.items())),
        "evidence": dict(sorted(evidence.items())),
        "samples": samples,
        "unresolved_count": len(unresolved), "unresolved": unresolved,
    }


def _raw_items(conn: sqlite3.Connection) -> dict:
    by_platform: Counter[str] = Counter()
    unresolved = 0
    for row in conn.execute(
        "select source_platform, captured_at, published_at, first_seen_at from raw_items"
    ):
        if repair_reason(row["captured_at"], row["published_at"]) is None:
            continue
        by_platform[row["source_platform"]] += 1
        if normalized_utc(row["first_seen_at"]) is None:
            unresolved += 1
    return {
        "needs_repair_raw_items": sum(by_platform.values()),
        "by_platform": dict(sorted(by_platform.items())),
        "unresolved_count": unresolved,
    }


def preview(db_path: Path, sample_limit: int = 24) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("pragma query_only=ON")
        return {"database": str(db_path), **_cards(conn, sample_limit), "raw_items": _raw_items(conn)}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--sample-limit", type=int, default=24)
    args = parser.parse_args()
    print(json.dumps(preview(args.database, max(0, args.sample_limit)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
