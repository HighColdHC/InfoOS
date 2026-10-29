"""Explicit, bounded reads used by data export workflows."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import re

from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path


class ExportQueries:
    def __init__(self, base_dir: Path | str):
        self.db_path = resolve_sqlite_path(base_dir)

    def estimate(self, request: dict[str, Any]) -> dict[str, int]:
        media_bytes = 0
        media_files = 0
        estimated_bytes = 0
        with connect_existing(self.db_path) as conn:
            if request["include_cards_content"]:
                card_where, card_params = self._time_filter_any(
                    request, ("published_at", "updated_at", "captured_at")
                )
                row = conn.execute(
                    """
                    select coalesce(sum(length(title) + length(content) + length(summary)), 0) as bytes
                    from cards where card_type = 'information' and
                    """ + card_where,
                    card_params,
                ).fetchone()
                estimated_bytes += int(row["bytes"])
            if request["include_sources_runs"]:
                row = conn.execute(
                    "select count(*) as count from sources"
                ).fetchone()
                estimated_bytes += row["count"] * 1024
            include_files = request["include_attachment_files"] or request["include_media_files"]
            if include_files:
                row = conn.execute(
                    """
                    select count(*) as count, coalesce(sum(size_bytes), 0) as bytes
                    from card_assets
                    where local_path is not null
                      and status in ('available','done','downloaded','enriched','preserved','ready','understood')
                      and cleanup_state = 'present'
                    """
                ).fetchone()
                media_files = row["count"]
                media_bytes = int(row["bytes"])
        return {
            "estimated_bytes": estimated_bytes + media_bytes,
            "file_count": 1 + media_files,
            "media_bytes": media_bytes,
        }

    def data_sections(self, request: dict[str, Any]) -> dict[str, Any]:
        sections: dict[str, Any] = {"schema": "infoos.export.v1"}
        with connect_existing(self.db_path) as conn:
            if request["include_sources_runs"]:
                sections["sources"] = self._rows(
                    conn,
                    """select source_id, platform, source_type, identifier, display_name,
                                      cadence_hours, notes, enabled, connector_status, connector_id,
                                      next_run_at, last_run_at, created_at, updated_at
                               from sources order by source_id""",
                )
                sections["source_config_versions"] = self._rows(
                    conn,
                    """select version_id, source_id, config_version, fetch_input_version,
                                      config_json, change_reason, created_at
                               from source_config_versions order by source_id, created_at""",
                )
                run_where, run_params = self._time_filter_any(
                    request, ("scheduled_for", "started_at", "finished_at")
                )
                sections["runs"] = self._rows(
                    conn,
                    """select run_id, source_id, connector_id, scheduled_for, started_at,
                                      finished_at, status, fetched_count, visible_count, new_count,
                                      skipped_count, dedup_skipped_count, error_count, duration_ms,
                                      next_run_at, attempt, trigger, error_summary
                               from run_history where """ + run_where + " order by finished_at, run_id",
                    run_params,
                )
                task_where, task_params = self._time_filter(request, "created_at")
                sections["tasks"] = self._rows(
                    conn,
                    """select task_id, task_type, target_kind, target_id, status, depends_on,
                                      attempt, max_attempts, next_run_at, error_reason,
                                      artifacts_summary, started_at, finished_at, input_version,
                                      result_version, last_failure_class, created_at, updated_at
                               from tasks where """ + task_where + " order by created_at, task_id",
                    task_params,
                )
                event_where, event_params = self._time_filter(request, "occurred_at")
                sections["task_events"] = self._rows(
                    conn,
                    """select event_id, task_id, chain_id, event_type, status, failure_class,
                                      payload, occurred_at
                               from task_events where """ + event_where + " order by occurred_at, event_id",
                    event_params,
                )
            if request["include_cards_content"]:
                card_where, card_params = self._time_filter_any(
                    request, ("published_at", "updated_at", "captured_at")
                )
                sections["cards"] = self._rows(
                    conn,
                    """select card_id, card_type, version, content_hash, updated_at, status,
                                      completeness_status, missing_reasons, processor_version,
                                      raw_connector_id, raw_item_id, source_run_id, source_id,
                                      source_platform, source_type, source_url, source_author, title,
                                      content, summary, primary_domain_id, secondary_domain_ids,
                                      published_at, captured_at, processed_at
                               from cards where card_type = 'information' and """ + card_where
                    + " order by updated_at, card_id",
                    card_params,
                )
                block_where, block_params = self._time_filter_any(
                    request, ("c.published_at", "c.updated_at", "c.captured_at")
                )
                sections["content_blocks"] = self._rows(
                    conn,
                    """select b.block_id, b.card_id, b.position, b.layer, b.block_kind,
                                      b.status, b.body, b.segments, b.source_url, b.provenance,
                                      b.created_at, b.updated_at
                               from card_content_blocks b join cards c on c.card_id = b.card_id
                               where c.card_type = 'information' and """ + block_where
                    + " order by b.card_id, b.position, b.block_id",
                    block_params,
                )
            if request["include_asset_index"]:
                sections["assets"] = self.asset_index(conn, request=request)
        return sections

    def attachment_rows(self, request: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        where, params = self._time_filter_any(
            request or {}, ("c.published_at", "c.updated_at", "c.captured_at")
        )
        with connect_existing(self.db_path) as conn:
            return self._rows(
                conn,
                """select a.asset_id, a.card_id, a.asset_kind, a.local_path, a.mime_type,
                                  a.size_bytes, a.content_hash, a.status, a.cleanup_state
                           from card_assets a join cards c on c.card_id = a.card_id
                           where c.card_type = 'information' and a.local_path is not null
                             and a.status in ('available','done','downloaded','enriched','preserved','ready','understood')
                             and a.cleanup_state = 'present'
                             and """ + where + " order by a.asset_id",
                params,
            )

    def resolve_public_source_run_id(self, public_id: str) -> str | None:
        """Resolve the frozen opaque Source Run identifier without importing a read model."""
        if re.fullmatch(r"task_[0-9a-f]{24}", public_id) is None:
            return None
        matched: str | None = None
        with connect_existing(self.db_path) as conn:
            rows = conn.execute(
                "select task_id from tasks where task_type = 'fetch' order by task_id"
            )
            for row in rows:
                task_id = str(row["task_id"])
                digest = hashlib.sha256(
                    f"infoos-source-run:task\0{task_id}".encode("utf-8")
                ).hexdigest()[:24]
                if f"task_{digest}" != public_id:
                    continue
                if matched is not None:
                    return None
                matched = task_id
        return matched

    @staticmethod
    def asset_index(
        conn: Any, *, request: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        where, params = ExportQueries._time_filter_any(
            request or {}, ("c.published_at", "c.updated_at", "c.captured_at")
        )
        return ExportQueries._rows(
            conn,
            """select a.asset_id, a.card_id, a.asset_kind, a.source_url, a.remote_url,
                              a.mime_type, a.size_bytes, a.content_hash, a.preservation_class,
                              a.status, a.cleanup_state, a.created_at, a.updated_at
                       from card_assets a join cards c on c.card_id = a.card_id
                       where c.card_type = 'information' and """ + where + " order by a.asset_id",
            params,
        )

    @staticmethod
    def _rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

    @staticmethod
    def _time_filter(request: dict[str, Any], column: str) -> tuple[str, tuple[Any, ...]]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if request.get("time_from"):
            clauses.append(f"julianday(source_timestamp({column})) >= julianday(?)")
            params.append(request["time_from"])
        if request.get("time_to"):
            clauses.append(f"julianday(source_timestamp({column})) <= julianday(?)")
            params.append(request["time_to"])
        return " and ".join(clauses), tuple(params)

    @staticmethod
    def _time_filter_any(
        request: dict[str, Any], columns: tuple[str, ...]
    ) -> tuple[str, tuple[Any, ...]]:
        if not request.get("time_from") and not request.get("time_to"):
            return "1 = 1", ()
        column_filters: list[str] = []
        params: list[Any] = []
        for column in columns:
            clauses = [f"{column} is not null", f"trim({column}) <> ''"]
            if request.get("time_from"):
                clauses.append(f"julianday(source_timestamp({column})) >= julianday(?)")
                params.append(request["time_from"])
            if request.get("time_to"):
                clauses.append(f"julianday(source_timestamp({column})) <= julianday(?)")
                params.append(request["time_to"])
            column_filters.append("(" + " and ".join(clauses) + ")")
        return "(" + " or ".join(column_filters) + ")", tuple(params)
