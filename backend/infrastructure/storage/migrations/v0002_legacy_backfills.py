"""Resumable legacy data backfills for migration v0002."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable


DEFAULT_BATCH_SIZE = 500


class LegacyMigrationMarkerError(RuntimeError):
    """A legacy migration marker exists with an unknown value."""


@dataclass(frozen=True)
class BatchResult:
    phase: str
    rows: int
    complete: bool
    cursor: str | None = None

    def as_event(self) -> dict[str, Any]:
        return {
            "migration": 2,
            "phase": self.phase,
            "rows": self.rows,
            "complete": self.complete,
            "cursor": self.cursor,
        }


_PHASES: tuple[tuple[str, str, Callable[[sqlite3.Connection, int], BatchResult]], ...] = ()


def _marker_value(conn: sqlite3.Connection, marker: str) -> str | None:
    row = conn.execute("select value from settings where key = ?", (marker,)).fetchone()
    if row is None:
        return None
    value = str(row[0])
    if value != "complete":
        raise LegacyMigrationMarkerError(
            f"legacy migration marker {marker!r} has unsupported value {value!r}; expected 'complete'"
        )
    return value


def _complete_marker(conn: sqlite3.Connection, marker: str) -> None:
    conn.execute(
        "insert into settings(key, value, updated_at) values (?, 'complete', datetime('now'))",
        (marker,),
    )


def _progress_key(phase: str, name: str = "cursor") -> str:
    return f"_migration.v0002.{phase}.{name}"


def _read_progress(conn: sqlite3.Connection, phase: str, name: str = "cursor") -> str | None:
    row = conn.execute(
        "select value from settings where key = ?", (_progress_key(phase, name),)
    ).fetchone()
    return None if row is None else str(row[0])


def _write_progress(conn: sqlite3.Connection, phase: str, value: str, name: str = "cursor") -> None:
    conn.execute(
        """
        insert into settings(key, value, updated_at) values (?, ?, datetime('now'))
        on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
        """,
        (_progress_key(phase, name), value),
    )


def _clear_progress(conn: sqlite3.Connection, phase: str) -> None:
    conn.execute("delete from settings where key like ?", (f"_migration.v0002.{phase}.%",))


def _versioned_card_insights(conn: sqlite3.Connection, batch_size: int) -> BatchResult:
    phase = "versioned_card_insights"
    rows = conn.execute(
        """
        select latest.card_id, latest.input_version
        from card_ai_insights latest
        left join card_ai_insight_versions versioned
          on versioned.card_id = latest.card_id and versioned.input_version = latest.input_version
        where latest.input_version is not null
          and trim(latest.input_version) <> ''
          and latest.status = 'succeeded'
          and versioned.card_id is null
        order by latest.card_id, latest.input_version
        limit ?
        """,
        (batch_size,),
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            insert or ignore into card_ai_insight_versions (
              card_id, input_version, main_point, topic_assignment, scores, score_reasons,
              why_it_matters, is_time_sensitive, evidence_quotes, status, last_error,
              model, prompt_version, generated_at
            )
            select card_id, input_version, main_point, topic_assignment, scores, score_reasons,
              why_it_matters, is_time_sensitive, evidence_quotes, status, last_error,
              model, prompt_version, generated_at
            from card_ai_insights where card_id = ? and input_version = ?
            """,
            (row[0], row[1]),
        )
    return BatchResult(phase, len(rows), len(rows) < batch_size)


def _source_calibration_versions(conn: sqlite3.Connection, batch_size: int) -> BatchResult:
    from backend.domain.sources.versioning import source_fetch_input_version

    phase = "source_calibration_versions"
    rows = conn.execute(
        "select * from sources where calibration_input_version is null order by source_id limit ?",
        (batch_size,),
    ).fetchall()
    for row in rows:
        source = dict(row)
        source["raw_metadata"] = json.loads(source.get("raw_metadata") or "{}")
        conn.execute(
            "update sources set calibration_input_version = ? where source_id = ? and calibration_input_version is null",
            (source_fetch_input_version(source), source["source_id"]),
        )
    return BatchResult(phase, len(rows), len(rows) < batch_size)


def _search_index(conn: sqlite3.Connection, batch_size: int) -> BatchResult:
    phase = "search_index_v1"
    marker = "migration.search_index.v1"
    state = _read_progress(conn, phase, "state")
    cursor = _read_progress(conn, phase)
    if state is None:
        _write_progress(conn, phase, "deleting", "state")
        return BatchResult(phase, 0, False, None)

    if state == "deleting":
        rows = conn.execute(
            "select rowid from infoos_search order by rowid limit ?", (batch_size,)
        ).fetchall()
        conn.executemany("delete from infoos_search where rowid = ?", ((row[0],) for row in rows))
        if len(rows) < batch_size:
            _write_progress(conn, phase, "cards", "state")
            _write_progress(conn, phase, "")
        return BatchResult(phase, len(rows), False, None)

    if state == "cards":
        rows = conn.execute(
            "select card_id, title, content, summary, source_author from cards where card_id > ? order by card_id limit ?",
            (cursor or "", batch_size),
        ).fetchall()
        for row in rows:
            conn.execute(
                "insert into infoos_search(entity_type, entity_id, title, body, source) values ('card', ?, ?, ? || ' ' || ?, ?)",
                (row[0], row[1], row[2], row[3], row[4]),
            )
        if rows:
            cursor = str(rows[-1][0])
            _write_progress(conn, phase, cursor)
        if len(rows) < batch_size:
            _write_progress(conn, phase, "topics", "state")
            _write_progress(conn, phase, "")
        return BatchResult(phase, len(rows), False, cursor)

    if state != "topics":
        raise LegacyMigrationMarkerError(f"unsupported {phase} progress state {state!r}")
    rows = conn.execute(
        "select topic_id, title, latest_progress, ai_interpretation, source_summary from topics where topic_id > ? order by topic_id limit ?",
        (cursor or "", batch_size),
    ).fetchall()
    for row in rows:
        conn.execute(
            "insert into infoos_search(entity_type, entity_id, title, body, source) values ('topic', ?, ?, ? || ' ' || ?, ?)",
            (row[0], row[1], row[2], row[3], row[4]),
        )
    if rows:
        cursor = str(rows[-1][0])
        _write_progress(conn, phase, cursor)
    complete = len(rows) < batch_size
    if complete:
        _complete_marker(conn, marker)
        _clear_progress(conn, phase)
    return BatchResult(phase, len(rows), complete, cursor)


def _load_card_children(
    conn: sqlite3.Connection, card_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blocks: list[dict[str, Any]] = []
    for block_row in conn.execute(
        "select * from card_content_blocks where card_id = ? order by position, created_at, block_id",
        (card_id,),
    ).fetchall():
        block = dict(block_row)
        try:
            block["segments"] = json.loads(block.get("segments") or "[]")
        except (TypeError, ValueError):
            block["segments"] = []
        blocks.append(block)
    assets = [
        dict(asset)
        for asset in conn.execute(
            "select * from card_assets where card_id = ? order by created_at, asset_id", (card_id,)
        ).fetchall()
    ]
    return blocks, assets


def _card_completeness(conn: sqlite3.Connection, batch_size: int) -> BatchResult:
    from backend.domain.information_cards import information_card_content_hash

    phase = "card_completeness_v2"
    marker = "migration.card_completeness.v2"
    rows = conn.execute(
        """
        select * from cards
        where completeness_status = 'partial'
          and json_valid(missing_reasons)
          and json_array_length(missing_reasons) = 1
          and json_extract(missing_reasons, '$[0]') = 'skipped_no_media_expected'
        order by card_id limit ?
        """,
        (batch_size,),
    ).fetchall()
    for row in rows:
        card = dict(row)
        blocks, assets = _load_card_children(conn, str(card["card_id"]))
        content_hash = information_card_content_hash(
            card, blocks, assets, completeness_status="complete", missing_reasons=[]
        )
        changed = card.get("content_hash") != content_hash
        conn.execute(
            """
            update cards set version = version + ?, content_hash = ?,
              updated_at = case when ? then strftime('%Y-%m-%dT%H:%M:%SZ', 'now') else updated_at end,
              status = 'active', completeness_status = 'complete', missing_reasons = '[]'
            where card_id = ?
            """,
            (1 if changed else 0, content_hash, 1 if changed else 0, card["card_id"]),
        )
    complete = len(rows) < batch_size
    if complete:
        _complete_marker(conn, marker)
    return BatchResult(phase, len(rows), complete)


def _x_card_enrichment(conn: sqlite3.Connection, batch_size: int) -> BatchResult:
    from backend.domain.card_titles import information_card_title
    from backend.domain.information_cards import information_card_content_hash
    from backend.domain.media_outcomes import is_resolved_x_no_media_outcome

    phase = "x_card_enrichment_v1"
    marker = "migration.x_card_enrichment.v1"
    cursor = _read_progress(conn, phase) or ""
    rows = conn.execute(
        "select * from cards where source_platform = 'x' and card_id > ? order by card_id limit ?",
        (cursor, batch_size),
    ).fetchall()
    for row in rows:
        card = dict(row)
        card_id = str(card["card_id"])
        changed_card = False
        if not " ".join(str(card.get("title") or "").split()):
            title = information_card_title("x", card.get("title"), card.get("content"))
            if title:
                conn.execute("update cards set title = ? where card_id = ?", (title, card_id))
                changed_card = True

        if str(card.get("status") or "") == "active" and str(card.get("completeness_status") or "") == "partial":
            try:
                missing_reasons = json.loads(card.get("missing_reasons") or "[]")
            except (TypeError, ValueError):
                missing_reasons = []
            normalized = (
                {str(reason).strip() for reason in missing_reasons if str(reason).strip()}
                if isinstance(missing_reasons, list)
                else set()
            )
            if normalized and normalized <= {"skipped_no_media_found", "x_media_download_no_media_found"}:
                task = conn.execute(
                    """
                    select status, artifacts_summary from tasks
                    where task_type = 'media_download' and target_kind = 'card' and target_id = ?
                    order by coalesce(finished_at, updated_at, created_at) desc, created_at desc, rowid desc
                    limit 1
                    """,
                    (card_id,),
                ).fetchone()
                try:
                    artifacts = json.loads(task["artifacts_summary"] or "{}") if task else {}
                except (TypeError, ValueError):
                    artifacts = {}
                if (
                    task is not None
                    and str(task["status"] or "") == "skipped"
                    and isinstance(artifacts, dict)
                    and is_resolved_x_no_media_outcome(
                        source_platform="x",
                        download_status=artifacts.get("download_status"),
                        candidate_count=artifacts.get("candidate_count"),
                        reasons=artifacts.get("gap_reasons") or (),
                    )
                ):
                    conn.execute(
                        """
                        update card_content_blocks set status = 'resolved',
                          raw_payload = json_set(coalesce(raw_payload, '{}'), '$.resolved', json('true')),
                          updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                        where card_id = ? and block_kind = 'media_download_gap' and status = 'blocked'
                          and json_valid(raw_payload)
                          and coalesce(json_extract(raw_payload, '$.reason'), json_extract(raw_payload, '$.block_reason'))
                            = 'x_media_download_no_media_found'
                        """,
                        (card_id,),
                    )
                    conn.execute(
                        "update cards set completeness_status = 'complete', missing_reasons = '[]' where card_id = ?",
                        (card_id,),
                    )
                    changed_card = True

        if changed_card:
            updated = dict(conn.execute("select * from cards where card_id = ?", (card_id,)).fetchone())
            if (
                str(updated.get("status") or "") == "active"
                and int(updated.get("version") or 0) >= 1
                and str(updated.get("content_hash") or "").strip()
            ):
                blocks, assets = _load_card_children(conn, card_id)
                try:
                    current_reasons = json.loads(updated.get("missing_reasons") or "[]")
                except (TypeError, ValueError):
                    current_reasons = []
                if not isinstance(current_reasons, list):
                    current_reasons = []
                content_hash = information_card_content_hash(
                    updated,
                    blocks,
                    assets,
                    completeness_status=str(updated.get("completeness_status") or "processing"),
                    missing_reasons=current_reasons,
                )
                changed = updated.get("content_hash") != content_hash
                conn.execute(
                    """
                    update cards set version = version + ?, content_hash = ?,
                      updated_at = case when ? then strftime('%Y-%m-%dT%H:%M:%SZ', 'now') else updated_at end
                    where card_id = ?
                    """,
                    (1 if changed else 0, content_hash, 1 if changed else 0, card_id),
                )
                conn.execute(
                    "delete from infoos_search where entity_type = 'card' and entity_id = ?", (card_id,)
                )
                conn.execute(
                    """
                    insert into infoos_search(entity_type, entity_id, title, body, source)
                    select 'card', card_id, title, content || ' ' || summary, source_author
                    from cards where card_id = ?
                    """,
                    (card_id,),
                )
    if rows:
        cursor = str(rows[-1]["card_id"])
        _write_progress(conn, phase, cursor)
    complete = len(rows) < batch_size
    if complete:
        _complete_marker(conn, marker)
        _clear_progress(conn, phase)
    return BatchResult(phase, len(rows), complete, cursor)


def _task_queues(conn: sqlite3.Connection, batch_size: int) -> BatchResult:
    from backend.domain.tasks import Task, TaskType
    from backend.infrastructure.storage.task_queue_policy import queue_policy_for

    phase = "task_queues_v1"
    marker = "migration.task_queues.v1"
    rows = conn.execute(
        """
        select task_id, task_type, target_kind, target_id, next_run_at, created_at
        from tasks where queue_name is null or priority is null or deadline_at is null
        order by task_id limit ?
        """,
        (batch_size,),
    ).fetchall()
    for row in rows:
        task = Task(
            task_id=str(row["task_id"]),
            task_type=TaskType(str(row["task_type"])),
            target_kind=str(row["target_kind"]),
            target_id=str(row["target_id"]),
            next_run_at=row["next_run_at"],
        )
        policy = queue_policy_for(conn, task, enqueued_at=str(row["created_at"]))
        conn.execute(
            "update tasks set queue_name = ?, priority = ?, deadline_at = ? where task_id = ?",
            (policy.queue_name, policy.priority, policy.deadline_at, task.task_id),
        )
    complete = len(rows) < batch_size
    if complete:
        _complete_marker(conn, marker)
    return BatchResult(phase, len(rows), complete)


_PHASES = (
    ("versioned_card_insights", "", _versioned_card_insights),
    ("source_calibration_versions", "", _source_calibration_versions),
    ("search_index_v1", "migration.search_index.v1", _search_index),
    ("card_completeness_v2", "migration.card_completeness.v2", _card_completeness),
    ("x_card_enrichment_v1", "migration.x_card_enrichment.v1", _x_card_enrichment),
    ("task_queues_v1", "migration.task_queues.v1", _task_queues),
)


def run_next_batch(conn: sqlite3.Connection, batch_size: int = DEFAULT_BATCH_SIZE) -> BatchResult | None:
    """Run one bounded v0002 batch, or return None when every backfill is complete."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    # Validate all legacy markers before changing any data. A marker with an
    # unknown value is evidence of an interrupted or foreign migration and must
    # never be interpreted as either pending or complete.
    for _, marker, _ in _PHASES:
        if marker:
            _marker_value(conn, marker)
    for phase, marker, handler in _PHASES:
        if marker and _marker_value(conn, marker) == "complete":
            continue
        result = handler(conn, batch_size)
        # Predicate-driven phases need no durable marker: once a committed
        # batch removes every matching row, the next transaction observes zero
        # and advances. Still return non-empty final batches so one write
        # transaction never combines multiple phase batches.
        if not marker and result.complete and result.rows == 0:
            continue
        return result
    return None
