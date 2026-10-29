from __future__ import annotations

import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
import pytest

from backend.api.middleware.plugin_request_audit import PluginRequestAuditMiddleware
from backend.application.usecases.api_tokens import create_token
from backend.infrastructure.security.plugin_auth import AuthContext
from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository
from backend.infrastructure.storage.api_tokens import ApiTokenRepository


def _app(base_dir, context: AuthContext) -> FastAPI:
    app = FastAPI()
    app.state.base_dir = base_dir

    @app.get("/api/plugin/v1/cards")
    def cards(request: Request):
        request.state.plugin_auth = context
        request.state.audit_meta = {
            "card_count": 2,
            "authorization": "Bearer must-not-be-stored",
            "body": "private card body must-not-be-stored",
        }
        return {"items": [{"title": "private card body must-not-be-stored"}]}

    @app.get("/api/plugin/v1/rejected")
    def rejected():
        return JSONResponse(
            {"code": "invalid_token", "message": "Invalid API token"},
            status_code=401,
        )

    @app.get("/api/plugin/v1/assets/demo")
    def ranged_asset(request: Request):
        request.state.plugin_auth = context
        request.state.audit_meta = {
            "asset_id": "demo",
            "response_bytes": 4,
            "range": "bytes 2-5/10",
        }
        return StreamingResponse(
            iter((b"23", b"45")),
            status_code=206,
            headers={
                "Content-Length": "4",
                "Content-Range": "bytes 2-5/10",
            },
        )

    @app.get("/ordinary")
    def ordinary():
        return {"ok": True}

    app.add_middleware(PluginRequestAuditMiddleware)
    return app


def _token_and_context(base_dir) -> tuple[dict, AuthContext]:
    created = create_token(base_dir, name="Obsidian Local", scopes=["cards:read", "assets:read"])
    token = created["token"]
    return created, AuthContext(
        token_id=token["token_id"],
        name=token["name"],
        token_prefix=token["token_prefix"],
        scopes=frozenset(token["scopes"]),
    )


def test_success_generates_safe_request_id_logs_metadata_and_updates_last_used_once(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, context = _token_and_context(tmp_path)
    calls: list[tuple[str, str | None]] = []
    original = ApiTokenRepository.update_last_used

    def record_update(self, token_id, *, used_at=None, connection=None):
        calls.append((token_id, used_at))
        return original(self, token_id, used_at=used_at, connection=connection)

    monkeypatch.setattr(ApiTokenRepository, "update_last_used", record_update)
    client = TestClient(_app(tmp_path, context))

    response = client.get(
        "/api/plugin/v1/cards",
        headers={
            "Authorization": f"Bearer {created['plaintext_token']}",
            "Cookie": "session=must-not-be-stored",
            "X-Request-ID": "../../invalid-request-id",
        },
    )

    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    assert request_id.startswith("req_")
    assert request_id != "../../invalid-request-id"
    detail = ApiRequestLogRepository(tmp_path).get_detail(request_id)
    assert detail is not None
    assert detail["status_code"] == 200
    assert detail["result_code"] == "cards_returned"
    assert detail["result_summary"] == "Returned 2 cards"
    assert detail["token_prefix"] == context.token_prefix
    assert detail["client_label"] == "Obsidian Local"
    assert detail["response_meta"]["card_count"] == 2
    assert detail["response_meta"]["response_bytes"] > 0
    assert set(detail["response_meta"]) == {"card_count", "response_bytes"}
    assert calls == [(context.token_id, detail["created_at"])]
    assert ApiTokenRepository(tmp_path).get_internal(context.token_id)["last_used_at"] == detail["created_at"]

    with sqlite3.connect(ApiRequestLogRepository(tmp_path).db_path) as conn:
        database_text = "\n".join(
            str(value) for row in conn.execute("select * from api_request_logs") for value in row
        )
    assert created["plaintext_token"] not in database_text
    assert "session=must-not-be-stored" not in database_text
    assert "private card body must-not-be-stored" not in database_text


def test_valid_client_request_id_is_preserved_and_auth_failure_is_logged(tmp_path) -> None:
    _, context = _token_and_context(tmp_path)
    client = TestClient(_app(tmp_path, context))

    response = client.get(
        "/api/plugin/v1/rejected",
        headers={"X-Request-ID": "req_client_123"},
    )

    assert response.status_code == 401
    assert response.headers["x-request-id"] == "req_client_123"
    detail = ApiRequestLogRepository(tmp_path).get_detail("req_client_123")
    assert detail is not None
    assert detail["status_code"] == 401
    assert detail["result_code"] == "unauthorized"
    assert detail["result_summary"] == "Authentication required"
    assert detail["token_prefix"] is None
    assert detail["client_label"] is None
    assert detail["response_meta"]["response_bytes"] > 0
    assert set(detail["response_meta"]) == {"response_bytes"}


def test_streaming_range_is_logged_from_headers_and_audit_meta_without_body_capture(tmp_path) -> None:
    _, context = _token_and_context(tmp_path)
    client = TestClient(_app(tmp_path, context))

    response = client.get(
        "/api/plugin/v1/assets/demo",
        headers={"X-Request-ID": "req_range_206"},
    )

    assert response.status_code == 206
    assert response.content == b"2345"
    detail = ApiRequestLogRepository(tmp_path).get_detail("req_range_206")
    assert detail is not None
    assert detail["result_code"] == "asset_range_returned"
    assert detail["result_summary"] == "Returned asset byte range"
    assert detail["response_meta"] == {
        "asset_id": "demo",
        "range": "bytes 2-5/10",
        "response_bytes": 4,
    }


def test_non_plugin_path_is_not_logged_or_given_request_id(tmp_path) -> None:
    _, context = _token_and_context(tmp_path)
    client = TestClient(_app(tmp_path, context))

    response = client.get("/ordinary", headers={"X-Request-ID": "req_ordinary"})

    assert response.status_code == 200
    assert "x-request-id" not in response.headers
    assert ApiRequestLogRepository(tmp_path).list(limit=10, offset=0)["total"] == 0


def test_log_and_last_used_share_one_transaction(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, context = _token_and_context(tmp_path)
    client = TestClient(_app(tmp_path, context))

    def fail_last_used(*_args, **_kwargs):
        raise sqlite3.IntegrityError("forced last-used failure")

    monkeypatch.setattr(ApiTokenRepository, "update_last_used", fail_last_used)

    with pytest.raises(sqlite3.IntegrityError, match="forced last-used failure"):
        client.get(
            "/api/plugin/v1/cards",
            headers={"X-Request-ID": "req_transaction"},
        )

    assert ApiRequestLogRepository(tmp_path).get_detail("req_transaction") is None
    assert ApiTokenRepository(tmp_path).get_internal(context.token_id)["last_used_at"] is None
