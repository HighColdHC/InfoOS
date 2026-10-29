"""Bounded, redacted audit records for plugin API requests."""
from __future__ import annotations

import json
import sqlite3
from statistics import median
from typing import Any, TypedDict, cast

from backend.domain.json_types import JsonObject
from backend.infrastructure.storage.base import InfoOSRepository, utc_now


class ApiRequestLogRow(TypedDict):
    request_id: str
    created_at: str
    method: str
    path: str
    status_code: int
    result_code: str
    result_summary: str
    token_prefix: str | None
    client_label: str | None
    duration_ms: int


class ApiRequestLogPage(TypedDict):
    items: list[ApiRequestLogRow]
    total: int
    next_offset: int | None


class ApiRequestLogDetail(ApiRequestLogRow):
    response_meta: object


class ApiRequestLogSummary(TypedDict):
    request_count: int
    success_2xx_count: int
    client_error_4xx_count: int
    median_duration_ms: int | float


_RESPONSE_META_FIELDS = frozenset(
    {
        "asset_id",
        "capture_id",
        "capture_mode",
        "card_count",
        "card_id",
        "content_hash",
        "disposition",
        "mcp_tool",
        "next_page_token",
        "page_size",
        "processing_status",
        "range",
        "request_bytes",
        "required_scope",
        "response_bytes",
        "version",
    }
)

_ITEM_COLUMNS = """
request_id, created_at, method, path, status_code, result_code,
result_summary, token_prefix, client_label, duration_ms
""".strip()

_DETAIL_COLUMNS = """
request_id, created_at, method, path, client_label, token_prefix,
status_code, duration_ms, result_code, result_summary, response_meta
""".strip()


class ApiRequestLogRepository(InfoOSRepository):
    """Store metadata-only request evidence; never headers or response bodies."""

    def append(
        self,
        *,
        request_id: str,
        token_id: str | None,
        token_prefix: str | None,
        client_label: str | None,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        response_bytes: int | None,
        result_code: str,
        result_summary: str,
        response_meta: JsonObject | None = None,
        created_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> ApiRequestLogDetail:
        values = {
            "request_id": request_id,
            "token_id": token_id,
            "token_prefix": token_prefix,
            "client_label": client_label,
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "response_bytes": response_bytes,
            "result_code": result_code,
            "result_summary": result_summary,
            "response_meta": response_meta,
            "created_at": created_at,
        }
        if connection is not None:
            return self.append_to_connection(connection, **values)
        with self.connect() as conn:
            return self.append_to_connection(conn, **values)

    @staticmethod
    def append_to_connection(
        conn: sqlite3.Connection,
        *,
        request_id: str,
        token_id: str | None,
        token_prefix: str | None,
        client_label: str | None,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        response_bytes: int | None,
        result_code: str,
        result_summary: str,
        response_meta: JsonObject | None = None,
        created_at: str | None = None,
    ) -> ApiRequestLogDetail:
        status = int(status_code)
        if not 100 <= status <= 599:
            raise ValueError("status_code must be between 100 and 599")
        conn.execute(
            """
            insert into api_request_logs (
              request_id, token_id, token_prefix, client_label, method, path,
              status_code, duration_ms, response_bytes, result_code,
              result_summary, response_meta, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _bounded_text(request_id, 128),
                _optional_text(token_id, 128),
                _optional_text(token_prefix, 32),
                _optional_text(client_label, 120),
                _bounded_text(method, 16).upper(),
                _bounded_text(path, 1024),
                status,
                max(0, int(duration_ms)),
                None if response_bytes is None else max(0, int(response_bytes)),
                _bounded_text(result_code, 80),
                _bounded_text(result_summary, 240),
                _encode_response_meta(response_meta),
                created_at or utc_now(),
            ),
        )
        row = conn.execute(
            f"select {_DETAIL_COLUMNS} from api_request_logs where request_id = ?",
            (_bounded_text(request_id, 128),),
        ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError("API request log was not stored")
        return _detail_row(row)

    def list(
        self,
        *,
        query: str | None = None,
        status: str | int | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> ApiRequestLogPage:
        where, params = _filter_clause(
            query=query,
            status=status,
            created_from=created_from,
            created_to=created_to,
        )
        bounded_limit = max(0, int(limit))
        bounded_offset = max(0, int(offset))
        with self.connect_existing() as conn:
            total = int(
                conn.execute(f"select count(*) from api_request_logs {where}", params).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                select {_ITEM_COLUMNS} from api_request_logs {where}
                order by created_at desc, request_id desc
                limit ? offset ?
                """,
                (*params, bounded_limit, bounded_offset),
            ).fetchall()
        items = [cast(ApiRequestLogRow, dict(row)) for row in rows]
        consumed = bounded_offset + len(items)
        return {
            "items": items,
            "total": total,
            "next_offset": consumed if consumed < total else None,
        }

    def summary(
        self,
        *,
        query: str | None = None,
        status: str | int | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> ApiRequestLogSummary:
        where, params = _filter_clause(
            query=query,
            status=status,
            created_from=created_from,
            created_to=created_to,
        )
        with self.connect_existing() as conn:
            rows = conn.execute(
                f"select status_code, duration_ms from api_request_logs {where}",
                params,
            ).fetchall()
        durations = [int(row["duration_ms"]) for row in rows]
        return {
            "request_count": len(rows),
            "success_2xx_count": sum(1 for row in rows if 200 <= int(row["status_code"]) < 300),
            "client_error_4xx_count": sum(1 for row in rows if 400 <= int(row["status_code"]) < 500),
            "median_duration_ms": median(durations) if durations else 0,
        }

    def get_detail(self, request_id: str) -> ApiRequestLogDetail | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                f"select {_DETAIL_COLUMNS} from api_request_logs where request_id = ?",
                (request_id,),
            ).fetchone()
        return _detail_row(row) if row is not None else None


def _filter_clause(
    *,
    query: str | None,
    status: str | int | None,
    created_from: str | None,
    created_to: str | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query and query.strip():
        clauses.append("(request_id like ? or path like ? or coalesce(client_label, '') like ?)")
        term = f"%{query.strip()}%"
        params.extend((term, term, term))
    status_clause, status_params = _status_filter(status)
    if status_clause:
        clauses.append(status_clause)
        params.extend(status_params)
    if created_from:
        clauses.append("created_at >= ?")
        params.append(created_from)
    if created_to:
        clauses.append("created_at <= ?")
        params.append(created_to)
    return (f"where {' and '.join(clauses)}" if clauses else "", params)


def _status_filter(status: str | int | None) -> tuple[str, list[Any]]:
    if status is None or str(status).strip().lower() in {"", "all"}:
        return "", []
    normalized = str(status).strip().lower()
    if normalized in {"2xx", "success"}:
        return "status_code between 200 and 299", []
    if normalized in {"4xx", "client_error"}:
        return "status_code between 400 and 499", []
    try:
        exact = int(normalized)
    except ValueError as exc:
        raise ValueError(f"unsupported status filter: {status}") from exc
    if not 100 <= exact <= 599:
        raise ValueError(f"unsupported status filter: {status}")
    return "status_code = ?", [exact]


def _encode_response_meta(value: JsonObject | None) -> str:
    safe: JsonObject = {}
    for key, item in (value or {}).items():
        if key not in _RESPONSE_META_FIELDS or not isinstance(item, (str, int, float, bool, type(None))):
            continue
        safe[key] = item[:256] if isinstance(item, str) else item
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _detail_row(row: sqlite3.Row) -> ApiRequestLogDetail:
    payload = dict(row)
    payload["response_meta"] = json.loads(payload["response_meta"])
    return cast(ApiRequestLogDetail, payload)


def _bounded_text(value: Any, limit: int) -> str:
    return str(value).strip()[:limit]


def _optional_text(value: Any, limit: int) -> str | None:
    return None if value is None else _bounded_text(value, limit)
