"""Offline raw-media conversion; no startup, migration or deployment hooks."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any

from backend.domain.feishu_content import compact_feishu_evidence
from backend.infrastructure.storage.database import connect_existing, connect_readonly, ensure_database_initialized
from backend.infrastructure.storage.raw_media import RawMediaStore

_TABLES = (("raw_items", ("connector_id", "raw_item_id")), ("cards", ("card_id",)))
_ForeignKeyViolation = tuple[str, int | None, str, int]


def _inline_count(payload: Any) -> int:
    if isinstance(payload, list):
        return sum(_inline_count(item) for item in payload)
    if not isinstance(payload, dict):
        return 0
    return sum(
        sum(isinstance(item, dict) and "base64" in item for item in value)
        if key == "media_blobs" and isinstance(value, list) else _inline_count(value)
        for key, value in payload.items()
    )


def _slim_evidence(payload: Any) -> Any:
    if isinstance(payload, list):
        return [_slim_evidence(item) for item in payload]
    if isinstance(payload, dict):
        return {key: compact_feishu_evidence(value) if key == "external_doc_read" and isinstance(value, dict)
                else _slim_evidence(value) for key, value in payload.items()}
    return payload


def _encode(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _references(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _references(item)
    elif isinstance(payload, dict):
        for key, value in payload.items():
            if key == "media_blobs" and isinstance(value, list):
                yield from (item for item in value if isinstance(item, dict) and "sha256" in item)
            else:
                yield from _references(value)


def _convert(payload: dict, table: str, store: RawMediaStore, *, write: bool) -> dict:
    for reference in _references(payload):
        store.read(reference)
    # Extract even duplicated legacy evidence before removing its redundant containers.
    converted = store.externalize(payload, write=write)
    slim = _slim_evidence(converted)
    if table == "cards":
        # Legacy card payloads keep their other fields; raw_items hold the media references.
        return slim
    # Legacy evidence may be the only copy of a reference. Preserve its identity
    # outside the status-only overlay, without duplicating the source record.
    retained = {_encode(ref) for ref in _references(slim)}
    missing = {_encode(ref): ref for ref in _references(converted) if _encode(ref) not in retained}
    if missing:
        metadata = slim.setdefault("raw_metadata", {})
        metadata["media_blobs"] = [*(metadata.get("media_blobs") or []), *missing.values()]
    return slim


def _page(conn: sqlite3.Connection, table: str, keys: tuple[str, ...], cursor: tuple | None,
          batch_size: int) -> list[sqlite3.Row]:
    fields = ", ".join(keys)
    where = f"where ({fields}) > ({', '.join('?' for _ in keys)})" if cursor is not None else ""
    return conn.execute(f"select {fields}, raw_payload from {table} {where} order by {fields} limit ?",
                        (*(cursor or ()), batch_size)).fetchall()


def _foreign_key_violations(conn: sqlite3.Connection) -> set[_ForeignKeyViolation]:
    return {tuple(row) for row in conn.execute("pragma foreign_key_check")}


def _validate(conn: sqlite3.Connection, baseline: set[_ForeignKeyViolation] | None = None) -> list[str]:
    integrity = [row[0] for row in conn.execute("pragma integrity_check")]
    if integrity != ["ok"] or (baseline is not None and _foreign_key_violations(conn) - baseline):
        raise RuntimeError("database integrity/foreign-key check failed; keep services stopped")
    return integrity


def maintain_raw_media(
    db_path: Path | str, *, apply: bool = False, services_stopped: bool = False,
    backup_verified: bool = False, batch_size: int = 100,
) -> dict[str, Any]:
    """Preview inline rows read-only; apply is explicitly offline and resumable.

    Files are published and hash-verified before updating each page in a short
    transaction. A crash may leave unreferenced files or committed earlier pages;
    rerunning is safe. No business identity, version, hash or task is rewritten.
    Existing foreign-key violations are reported; only new violations fail validation.
    """
    if apply and not (services_stopped and backup_verified):
        raise ValueError("--apply requires --services-stopped and --backup-verified")
    if not 1 <= batch_size <= 500:
        raise ValueError("batch_size must be between 1 and 500")
    path = ensure_database_initialized(db_path)
    store = RawMediaStore(path.parent)
    result: dict[str, Any] = {"mode": "applied" if apply else "dry-run", "db": str(path),
                              "raw_media_root": str(store.root), "tables": {}}
    opener = connect_existing if apply else connect_readonly
    with opener(path) as conn:
        if apply:
            conn.execute("pragma locking_mode=exclusive")
            conn.execute("pragma synchronous=full")
            conn.execute("begin exclusive")
            _validate(conn)
        else:
            conn.execute("begin")
        baseline = _foreign_key_violations(conn)
        counts = Counter((table, parent) for table, _rowid, parent, _fkid in baseline)
        result["foreign_key_violations_baseline"] = [
            {"table": table, "parent": parent, "count": count}
            for (table, parent), count in sorted(counts.items())
        ]
        if apply:
            conn.commit()  # Retain exclusive lock between file writes and page commits.
        for table, keys in _TABLES:
            summary = {"rows": [], "inline_rows": 0, "inline_blobs": 0,
                       "payload_bytes_before": 0, "payload_bytes_after": 0, "releasable_bytes": 0}
            cursor = None
            while rows := _page(conn, table, keys, cursor, batch_size):
                updates = []
                for row in rows:
                    cursor = tuple(row[key] for key in keys)
                    old = row["raw_payload"]
                    payload = json.loads(old)
                    count = _inline_count(payload)
                    if not count:
                        continue
                    converted = _convert(payload, table, store, write=apply)
                    encoded = _encode(converted)
                    if _inline_count(converted):
                        raise RuntimeError("inline media remains after conversion")
                    before, after = len(old.encode("utf-8")), len(encoded.encode("utf-8"))
                    summary["rows"].append({"key": dict(zip(keys, cursor)), "inline_blobs": count,
                                            "before_bytes": before, "after_bytes": after})
                    summary["inline_rows"] += 1
                    summary["inline_blobs"] += count
                    summary["payload_bytes_before"] += before
                    summary["payload_bytes_after"] += after
                    updates.append((encoded, *cursor, old))
                if apply and updates:
                    conn.execute("begin immediate")
                    try:
                        where = " and ".join(f"{key} = ?" for key in keys)
                        for update in updates:
                            changed = conn.execute(f"update {table} set raw_payload = ? where {where} and raw_payload = ?",
                                                   update).rowcount
                            if changed != 1:
                                raise RuntimeError("payload changed during offline maintenance")
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
            summary["releasable_bytes"] = max(0, summary["payload_bytes_before"] - summary["payload_bytes_after"])
            result["tables"][table] = summary
        if apply:
            result["integrity_check"] = _validate(conn, baseline)
            checkpoint = tuple(conn.execute("pragma wal_checkpoint(truncate)").fetchone())
            if checkpoint[0] != 0:
                raise RuntimeError("WAL checkpoint incomplete; keep services stopped")
            result["wal_checkpoint"] = checkpoint
        result["note"] = "Payload byte savings only; no VACUUM or physical disk reclamation."
    return result
