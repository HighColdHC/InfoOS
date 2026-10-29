from __future__ import annotations

from hashlib import sha256
import json
import sqlite3

import pytest

from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository
from backend.infrastructure.storage.api_tokens import ApiTokenRepository
from backend.infrastructure.storage.database import connect_writable


def _append_log(
    repo: ApiRequestLogRepository,
    *,
    request_id: str,
    status_code: int,
    duration_ms: int,
    created_at: str,
    path: str = "/api/plugin/v1/cards",
    client_label: str | None = "Obsidian",
    token_id: str | None = "token-1",
    result_code: str = "cards_returned",
    result_summary: str = "Returned card metadata",
    response_meta: dict[str, object] | None = None,
) -> dict[str, object]:
    return repo.append(
        request_id=request_id,
        token_id=token_id,
        token_prefix="ios_plaintex…" if token_id else None,
        client_label=client_label,
        method="GET",
        path=path,
        status_code=status_code,
        duration_ms=duration_ms,
        response_bytes=128,
        result_code=result_code,
        result_summary=result_summary,
        response_meta=response_meta or {"card_count": 2, "response_bytes": 128},
        created_at=created_at,
    )


def test_success_and_failure_logs_round_trip_with_bounded_detail(tmp_path) -> None:
    repo = ApiRequestLogRepository(tmp_path)
    success = _append_log(
        repo,
        request_id="req-success",
        status_code=200,
        duration_ms=12,
        created_at="2026-07-16T01:00:00Z",
    )
    failure = _append_log(
        repo,
        request_id="req-failure",
        token_id=None,
        status_code=401,
        duration_ms=4,
        created_at="2026-07-16T02:00:00Z",
        client_label=None,
        result_code="missing_token",
        result_summary="Authentication required",
        response_meta={"card_count": 0},
    )

    assert repo.get_detail("req-success") == success
    assert repo.get_detail("req-failure") == failure
    assert failure["token_prefix"] is None
    assert failure["response_meta"] == {"card_count": 0}


def test_list_pagination_filters_and_summary_use_the_same_predicate(tmp_path) -> None:
    repo = ApiRequestLogRepository(tmp_path)
    rows = [
        ("req-200", 200, 10, "2026-07-16T01:00:00Z", "/api/plugin/v1/cards", "Obsidian"),
        ("req-206", 206, 20, "2026-07-16T02:00:00Z", "/api/plugin/v1/assets/a", "Obsidian"),
        ("req-401", 401, 30, "2026-07-16T03:00:00Z", "/api/plugin/v1/cards", "Unknown"),
        ("req-404", 404, 40, "2026-07-16T04:00:00Z", "/api/plugin/v1/cards/missing", "Obsidian"),
        ("req-500", 500, 50, "2026-07-16T05:00:00Z", "/api/plugin/v1/cards", "Obsidian"),
    ]
    for request_id, status, duration, created_at, path, client in rows:
        _append_log(
            repo,
            request_id=request_id,
            status_code=status,
            duration_ms=duration,
            created_at=created_at,
            path=path,
            client_label=client,
        )

    page = repo.list(
        query="Obsidian",
        created_from="2026-07-16T01:00:00Z",
        created_to="2026-07-16T04:00:00Z",
        limit=2,
        offset=0,
    )
    summary = repo.summary(
        query="Obsidian",
        created_from="2026-07-16T01:00:00Z",
        created_to="2026-07-16T04:00:00Z",
    )
    client_errors = repo.summary(status="4xx")
    exact = repo.list(status="206", limit=10, offset=0)

    assert [item["request_id"] for item in page["items"]] == ["req-404", "req-206"]
    assert page["total"] == 3
    assert page["next_offset"] == 2
    assert summary == {
        "request_count": 3,
        "success_2xx_count": 2,
        "client_error_4xx_count": 1,
        "median_duration_ms": 20,
    }
    assert client_errors == {
        "request_count": 2,
        "success_2xx_count": 0,
        "client_error_4xx_count": 2,
        "median_duration_ms": 35,
    }
    assert [item["request_id"] for item in exact["items"]] == ["req-206"]


def test_repository_rejects_headers_and_body_and_filters_response_meta(tmp_path) -> None:
    repo = ApiRequestLogRepository(tmp_path)
    complete_token = "ios_complete-token-must-not-appear"
    complete_cookie = "session=complete-cookie-must-not-appear"
    complete_body = "private full information card body must not appear"

    for forbidden_key, forbidden_value in (
        ("authorization", f"Bearer {complete_token}"),
        ("cookie", complete_cookie),
        ("response_body", complete_body),
    ):
        with pytest.raises(TypeError, match=forbidden_key):
            repo.append(
                request_id=f"req-{forbidden_key}",
                token_id=None,
                token_prefix=None,
                client_label=None,
                method="GET",
                path="/api/plugin/v1/cards",
                status_code=401,
                duration_ms=1,
                response_bytes=None,
                result_code="invalid_token",
                result_summary="Rejected request",
                response_meta={},
                **{forbidden_key: forbidden_value},
            )

    stored = _append_log(
        repo,
        request_id="req-safe",
        status_code=200,
        duration_ms=3,
        created_at="2026-07-16T01:00:00Z",
        response_meta={
            "card_id": "card-1",
            "version": 3,
            "authorization": f"Bearer {complete_token}",
            "cookie": complete_cookie,
            "body": complete_body,
        },
    )

    assert stored["response_meta"] == {"card_id": "card-1", "version": 3}
    with sqlite3.connect(repo.db_path) as conn:
        database_text = "\n".join(
            str(value) for row in conn.execute("select * from api_request_logs") for value in row
        )
    assert complete_token not in database_text
    assert complete_cookie not in database_text
    assert complete_body not in database_text


def test_request_history_remains_after_token_is_revoked(tmp_path) -> None:
    token_repo = ApiTokenRepository(tmp_path)
    digest = sha256(b"ios_history-token").hexdigest()
    token_repo.append(
        token_id="token-history",
        name="History",
        token_prefix="ios_history…",
        token_digest=digest,
        scopes=["cards:read"],
        created_at="2026-07-16T01:00:00Z",
    )
    log_repo = ApiRequestLogRepository(tmp_path)
    _append_log(
        log_repo,
        request_id="req-history",
        token_id="token-history",
        status_code=200,
        duration_ms=8,
        created_at="2026-07-16T02:00:00Z",
    )

    token_repo.revoke("token-history", revoked_at="2026-07-16T03:00:00Z")

    assert log_repo.get_detail("req-history") is not None
    assert log_repo.get_detail("req-history")["token_prefix"] == "ios_plaintex…"


def test_append_to_connection_uses_callers_transaction_without_committing(tmp_path) -> None:
    repo = ApiRequestLogRepository(tmp_path)
    conn = connect_writable(repo.db_path)
    try:
        ApiRequestLogRepository.append_to_connection(
            conn,
            request_id="req-transaction",
            token_id=None,
            token_prefix=None,
            client_label=None,
            method="GET",
            path="/api/plugin/v1/health",
            status_code=200,
            duration_ms=1,
            response_bytes=20,
            result_code="ready",
            result_summary="Service ready",
            response_meta={"response_bytes": 20},
            created_at="2026-07-16T01:00:00Z",
        )
        assert conn.in_transaction is True
        conn.rollback()
    finally:
        conn.close()

    assert repo.get_detail("req-transaction") is None


def test_response_meta_is_stored_as_json_not_python_repr(tmp_path) -> None:
    repo = ApiRequestLogRepository(tmp_path)
    _append_log(
        repo,
        request_id="req-json",
        status_code=200,
        duration_ms=1,
        created_at="2026-07-16T01:00:00Z",
        response_meta={"card_count": 2, "range": "bytes 0-99/100"},
    )
    with sqlite3.connect(repo.db_path) as conn:
        raw = conn.execute(
            "select response_meta from api_request_logs where request_id='req-json'"
        ).fetchone()[0]

    assert json.loads(raw) == {"card_count": 2, "range": "bytes 0-99/100"}
