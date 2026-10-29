"""Hashed plugin API token persistence."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from backend.infrastructure.storage.api_tokens_types import ApiTokenRow, InternalApiTokenRow

from backend.infrastructure.storage.base import InfoOSRepository, utc_now


class ApiTokenRepository(InfoOSRepository):
    """Persist token metadata and digests without ever receiving plaintext."""

    def append(
        self,
        *,
        token_id: str,
        name: str,
        token_prefix: str,
        token_digest: str,
        scopes: list[str] | tuple[str, ...],
        webhook_enabled: bool = False,
        webhook_include_backfill: bool = False,
        webhook_url: str | None = None,
        webhook_initial_status: str | None = None,
        created_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> InternalApiTokenRow:
        values = {
            "token_id": token_id,
            "name": name,
            "token_prefix": token_prefix,
            "token_digest": token_digest,
            "scopes": scopes,
            "created_at": created_at,
            "webhook_enabled": webhook_enabled,
            "webhook_include_backfill": webhook_include_backfill,
            "webhook_url": webhook_url,
            "webhook_initial_status": webhook_initial_status,
        }
        if connection is not None:
            return self.create_to_connection(connection, **values)
        with self.connect() as conn:
            return self.create_to_connection(conn, **values)

    @staticmethod
    def create_to_connection(
        conn: sqlite3.Connection,
        *,
        token_id: str,
        name: str,
        token_prefix: str,
        token_digest: str,
        scopes: list[str] | tuple[str, ...],
        webhook_enabled: bool = False,
        webhook_include_backfill: bool = False,
        webhook_url: str | None = None,
        webhook_initial_status: str | None = None,
        created_at: str | None = None,
    ) -> InternalApiTokenRow:
        conn.execute(
            """
            insert into api_tokens (
              token_id, name, token_prefix, token_digest, scopes, created_at,
              revoked_at, last_used_at, replaced_by_token_id,
              webhook_enabled, webhook_include_backfill, webhook_url, config_revision,
              webhook_config_generation,
              webhook_delivery_status,
              webhook_consecutive_failures, webhook_last_attempt_at,
              webhook_last_success_at, webhook_last_failure_at,
              webhook_last_error, webhook_circuit_opened_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, null, null, null, ?, ?, ?, 1, 1, ?, 0,
              null, null, null, null, null, ?)
            """,
            (
                str(token_id),
                _bounded_text(name, 160),
                _bounded_text(token_prefix, 32),
                str(token_digest),
                _encode_scopes(scopes),
                created_at or utc_now(),
                1 if webhook_enabled else 0,
                1 if webhook_include_backfill else 0,
                webhook_url,
                webhook_initial_status
                or ("active" if webhook_enabled and webhook_url else "inactive"),
                created_at or utc_now(),
            ),
        )
        row = conn.execute("select * from api_tokens where token_id = ?", (token_id,)).fetchone()
        return _internal_row(row)

    def find_by_digest(self, token_digest: str) -> InternalApiTokenRow | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                "select * from api_tokens where token_digest = ?",
                (token_digest,),
            ).fetchone()
        return _internal_row(row) if row is not None else None

    def get_internal(self, token_id: str) -> InternalApiTokenRow | None:
        with self.connect_existing() as conn:
            row = conn.execute("select * from api_tokens where token_id = ?", (token_id,)).fetchone()
        return _internal_row(row) if row is not None else None

    def list_tokens(self, *, limit: int = 100, offset: int = 0) -> list[ApiTokenRow]:
        with self.connect_existing() as conn:
            rows = conn.execute(
                """
                select token_id, name, token_prefix, scopes, created_at,
                  revoked_at, last_used_at, replaced_by_token_id,
                  webhook_enabled, webhook_include_backfill, webhook_url, webhook_config_generation,
                  webhook_delivery_status,
                  webhook_consecutive_failures, webhook_last_attempt_at,
                  webhook_last_success_at, webhook_last_failure_at,
                  webhook_last_error, webhook_circuit_opened_at, updated_at
                from api_tokens
                order by created_at desc, token_id desc
                limit ? offset ?
                """,
                (max(0, int(limit)), max(0, int(offset))),
            ).fetchall()
        return [_management_row(row) for row in rows]

    def revoke(
        self,
        token_id: str,
        *,
        revoked_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> InternalApiTokenRow | None:
        if connection is not None:
            return self._revoke_to_connection(connection, token_id, revoked_at=revoked_at)
        with self.connect() as conn:
            return self._revoke_to_connection(conn, token_id, revoked_at=revoked_at)

    @staticmethod
    def _revoke_to_connection(
        conn: sqlite3.Connection,
        token_id: str,
        *,
        revoked_at: str | None,
    ) -> InternalApiTokenRow | None:
        timestamp = revoked_at or utc_now()
        conn.execute(
            """update api_tokens
               set updated_at = case when revoked_at is null then ? else updated_at end,
                   revoked_at = coalesce(revoked_at, ?)
               where token_id = ?""",
            (timestamp, timestamp, token_id),
        )
        return _read_internal(conn, token_id)

    def update_last_used(
        self,
        token_id: str,
        *,
        used_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> InternalApiTokenRow | None:
        if connection is not None:
            return self._update_last_used_to_connection(connection, token_id, used_at=used_at)
        with self.connect() as conn:
            return self._update_last_used_to_connection(conn, token_id, used_at=used_at)

    @staticmethod
    def _update_last_used_to_connection(
        conn: sqlite3.Connection,
        token_id: str,
        *,
        used_at: str | None,
    ) -> InternalApiTokenRow | None:
        conn.execute(
            "update api_tokens set last_used_at = ? where token_id = ?",
            (used_at or utc_now(), token_id),
        )
        return _read_internal(conn, token_id)

    def mark_replaced(
        self,
        token_id: str,
        *,
        replaced_by_token_id: str,
        revoked_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> InternalApiTokenRow | None:
        if connection is not None:
            return self.replace_to_connection(
                connection,
                token_id,
                replaced_by_token_id=replaced_by_token_id,
                revoked_at=revoked_at,
            )
        with self.connect() as conn:
            return self.replace_to_connection(
                conn,
                token_id,
                replaced_by_token_id=replaced_by_token_id,
                revoked_at=revoked_at,
            )

    @staticmethod
    def replace_to_connection(
        conn: sqlite3.Connection,
        token_id: str,
        *,
        replaced_by_token_id: str,
        revoked_at: str | None = None,
        expected_revision: int | None = None,
    ) -> InternalApiTokenRow | None:
        timestamp = revoked_at or utc_now()
        revision_clause = " and config_revision = ?" if expected_revision is not None else ""
        params: list[Any] = [timestamp, replaced_by_token_id, timestamp, token_id]
        if expected_revision is not None:
            params.append(expected_revision)
        cursor = conn.execute(
            f"""
            update api_tokens
            set revoked_at = ?, replaced_by_token_id = ?, updated_at = ?
            where token_id = ? and revoked_at is null
              and replaced_by_token_id is null{revision_clause}
            """,
            params,
        )
        return _read_internal(conn, token_id) if cursor.rowcount else None

    def update_configuration(
        self,
        token_id: str,
        *,
        name: str,
        scopes: list[str] | tuple[str, ...],
        webhook_enabled: bool,
        webhook_include_backfill: bool = False,
        webhook_url: str | None,
        expected_revision: int | None = None,
        reset_webhook_health: bool = True,
        delivery_status: str | None = None,
        updated_at: str | None = None,
    ) -> InternalApiTokenRow | None:
        with self.connect() as conn:
            revision_clause = " and config_revision = ?" if expected_revision is not None else ""
            assignments = [
                "name = ?", "scopes = ?", "webhook_enabled = ?", "webhook_include_backfill = ?", "webhook_url = ?",
                "config_revision = config_revision + 1", "updated_at = ?",
            ]
            params: list[Any] = [
                _bounded_text(name, 160),
                _encode_scopes(scopes),
                1 if webhook_enabled else 0,
                1 if webhook_include_backfill else 0,
                webhook_url,
                updated_at or utc_now(),
            ]
            if reset_webhook_health:
                assignments.extend([
                    "webhook_config_generation = webhook_config_generation + 1",
                    "webhook_delivery_status = ?", "webhook_consecutive_failures = 0",
                    "webhook_last_attempt_at = null", "webhook_last_success_at = null",
                    "webhook_last_failure_at = null", "webhook_last_error = null",
                    "webhook_circuit_opened_at = null",
                ])
                params.append(
                    delivery_status or ("active" if webhook_enabled and webhook_url else "inactive")
                )
            params.append(token_id)
            if expected_revision is not None:
                params.append(int(expected_revision))
            cursor = conn.execute(
                "update api_tokens set " + ", ".join(assignments)
                + " where token_id = ? and revoked_at is null"
                + " and replaced_by_token_id is null" + revision_clause,
                tuple(params),
            )
            return _read_internal(conn, token_id) if cursor.rowcount else None

    def record_webhook_success(
        self,
        token_id: str,
        *,
        attempted_at: str | None = None,
        expected_generation: int | None = None,
        allow_recovery: bool = False,
    ) -> InternalApiTokenRow | None:
        timestamp = attempted_at or utc_now()
        with self.connect() as conn:
            generation_clause = " and webhook_config_generation = ?" if expected_generation is not None else ""
            state_clause = "" if allow_recovery else " and webhook_delivery_status = 'active'"
            params: list[Any] = [1 if allow_recovery else 0, timestamp, timestamp, timestamp, token_id]
            if expected_generation is not None:
                params.append(int(expected_generation))
            cursor = conn.execute(
                """
                update api_tokens set
                  webhook_config_generation = webhook_config_generation + case
                    when ? = 1 and webhook_delivery_status in ('paused', 'pending_verification')
                      then 1 else 0 end,
                  webhook_delivery_status = case
                    when webhook_enabled = 1 and webhook_url is not null then 'active'
                    else 'inactive' end,
                  webhook_consecutive_failures = 0,
                  webhook_last_attempt_at = ?, webhook_last_success_at = ?,
                  webhook_last_failure_at = null, webhook_last_error = null,
                  webhook_circuit_opened_at = null, updated_at = ?
                where token_id = ? and revoked_at is null
                  and replaced_by_token_id is null
                """ + generation_clause + state_clause,
                tuple(params),
            )
            return _read_internal(conn, token_id) if cursor.rowcount else None

    def record_webhook_failure(
        self,
        token_id: str,
        *,
        error: str,
        attempted_at: str | None = None,
        circuit_threshold: int = 3,
        expected_generation: int | None = None,
    ) -> InternalApiTokenRow | None:
        timestamp = attempted_at or utc_now()
        threshold = max(1, int(circuit_threshold))
        with self.connect() as conn:
            generation_clause = " and webhook_config_generation = ?" if expected_generation is not None else ""
            params: list[Any] = [
                threshold,
                timestamp,
                timestamp,
                _bounded_text(error, 500),
                threshold,
                timestamp,
                timestamp,
                token_id,
            ]
            if expected_generation is not None:
                params.append(int(expected_generation))
            cursor = conn.execute(
                """
                update api_tokens set
                  webhook_consecutive_failures = webhook_consecutive_failures + 1,
                  webhook_delivery_status = case
                    when webhook_consecutive_failures + 1 >= ? then 'paused'
                    when webhook_enabled = 1 and webhook_url is not null then 'active'
                    else 'inactive' end,
                  webhook_last_attempt_at = ?, webhook_last_failure_at = ?,
                  webhook_last_error = ?,
                  webhook_circuit_opened_at = case
                    when webhook_consecutive_failures + 1 >= ?
                      then coalesce(webhook_circuit_opened_at, ?)
                    else webhook_circuit_opened_at end,
                  updated_at = ?
                where token_id = ? and revoked_at is null
                  and replaced_by_token_id is null
                  and webhook_delivery_status = 'active'
                """ + generation_clause,
                tuple(params),
            )
            return _read_internal(conn, token_id) if cursor.rowcount else None


def _read_internal(conn: sqlite3.Connection, token_id: str) -> InternalApiTokenRow | None:
    row = conn.execute("select * from api_tokens where token_id = ?", (token_id,)).fetchone()
    return _internal_row(row) if row is not None else None


def _internal_row(row: sqlite3.Row) -> InternalApiTokenRow:
    payload = dict(row)
    payload["scopes"] = _decode_scopes(payload["scopes"])
    payload["webhook_enabled"] = bool(payload["webhook_enabled"])
    payload["webhook_include_backfill"] = bool(payload["webhook_include_backfill"])
    return cast(InternalApiTokenRow, payload)


def _management_row(row: sqlite3.Row) -> ApiTokenRow:
    payload = dict(row)
    payload["scopes"] = _decode_scopes(payload["scopes"])
    payload["webhook_enabled"] = bool(payload["webhook_enabled"])
    payload["webhook_include_backfill"] = bool(payload["webhook_include_backfill"])
    return cast(ApiTokenRow, payload)


def _encode_scopes(scopes: list[str] | tuple[str, ...]) -> str:
    normalized = sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _decode_scopes(value: str) -> list[str]:
    decoded = json.loads(value)
    return [str(scope) for scope in decoded] if isinstance(decoded, list) else []


def _bounded_text(value: Any, limit: int) -> str:
    return str(value).strip()[:limit]
