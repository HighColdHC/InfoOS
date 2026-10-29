from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository
from backend.infrastructure.storage.api_tokens import ApiTokenRepository
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.base import utc_now


def test_token_admin_lifecycle_never_returns_or_persists_digest(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))

    overview = client.get("/api/token-auth/overview")
    assert overview.status_code == 200
    assert overview.json() == {
        "interface_path": "/api/plugin/v1",
        "interface_version": "v1",
        "service_status": "ready",
        "capabilities": [
            "cards:read", "assets:read", "captures:write",
            "intelligence:read", "profile:write", "evaluation:write", "feedback:write",
            "collect:write",
            "sources:write",
            "verification:write",
        ],
    }

    created = client.post(
        "/api/token-auth/tokens",
        json={"name": "Obsidian", "scopes": ["cards:read", "assets:read"]},
    )
    assert created.status_code == 201
    created_body = created.json()
    plaintext = created_body["plaintext_token"]
    token = created_body["token"]
    assert plaintext.startswith("ios_")
    assert token["status"] == "active"
    assert "token_digest" not in token

    listed = client.get("/api/token-auth/tokens")
    assert listed.status_code == 200
    assert listed.json()["items"] == [token]
    assert plaintext not in str(listed.json())

    rotated = client.post(f"/api/token-auth/tokens/{token['token_id']}/rotate")
    assert rotated.status_code == 201
    rotated_body = rotated.json()
    assert rotated_body["plaintext_token"] != plaintext
    assert rotated_body["token"]["status"] == "active"

    old = ApiTokenRepository(tmp_path).get_internal(token["token_id"])
    assert old is not None
    assert old["replaced_by_token_id"] == rotated_body["token"]["token_id"]
    assert old["revoked_at"] is not None

    revoked = client.post(
        f"/api/token-auth/tokens/{rotated_body['token']['token_id']}/revoke"
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert client.post("/api/token-auth/tokens/missing/revoke").status_code == 404

    stored = ApiTokenRepository(tmp_path).list_tokens()
    assert plaintext not in str(stored)
    assert rotated_body["plaintext_token"] not in str(stored)


def test_request_log_list_summary_filters_and_detail_share_one_scope(tmp_path) -> None:
    repository = ApiRequestLogRepository(tmp_path)
    now = utc_now()
    repository.append(
        request_id="req_ok",
        token_id="token_1",
        token_prefix="ios_example…",
        client_label="Obsidian desktop",
        method="GET",
        path="/api/plugin/v1/cards",
        status_code=200,
        duration_ms=20,
        response_bytes=300,
        result_code="cards_listed",
        result_summary="Returned 2 cards",
        response_meta={"card_count": 2, "page_size": 100},
        created_at=now,
    )
    repository.append(
        request_id="req_unauthorized",
        token_id=None,
        token_prefix=None,
        client_label="Obsidian desktop",
        method="GET",
        path="/api/plugin/v1/cards",
        status_code=401,
        duration_ms=10,
        response_bytes=80,
        result_code="invalid_token",
        result_summary="Invalid API token",
        response_meta={},
        created_at=now,
    )
    client = TestClient(create_app(tmp_path))

    filtered = client.get(
        "/api/token-auth/request-logs",
        params={"status": "2xx", "time_range": "24h", "limit": 25, "offset": 0},
    )
    assert filtered.status_code == 200
    body = filtered.json()
    assert body["summary"] == {
        "request_count": 1,
        "success_2xx_count": 1,
        "client_error_4xx_count": 0,
        "median_duration_ms": 20,
    }
    assert body["total"] == 1
    assert [item["request_id"] for item in body["items"]] == ["req_ok"]

    detail = client.get("/api/token-auth/request-logs/req_ok")
    assert detail.status_code == 200
    assert detail.json()["response_meta"] == {"card_count": 2, "page_size": 100}
    assert "response body" not in str(detail.json()).lower()

    missing = client.get("/api/token-auth/request-logs/missing")
    assert missing.status_code == 404
    assert missing.json()["code"] == "request_log_not_found"


def test_token_admin_rejects_invalid_scopes_with_stable_error(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/api/token-auth/tokens",
        json={"name": "Unsafe", "scopes": ["cards:write"]},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_token_request"


def test_token_webhook_create_edit_and_rotate_inherit_configuration(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.application.usecases.api_tokens._probe_or_raise",
        lambda *_args, **_kwargs: 204,
    )
    client = TestClient(create_app(tmp_path))
    created = client.post(
        "/api/token-auth/tokens",
        json={
            "name": "Webhook client",
            "scopes": ["cards:read"],
            "webhook_enabled": True,
            "webhook_url": "http://127.0.0.1:27124/infoos/webhook",
        },
    )
    assert created.status_code == 201
    token = created.json()["token"]
    assert token["webhook_enabled"] is True
    assert token["webhook_delivery_status"] == "pending_verification"
    assert token["webhook_config_generation"] == 1

    edited = client.patch(
        f"/api/token-auth/tokens/{token['token_id']}",
        json={
            "name": "Renamed webhook client",
            "scopes": ["cards:read", "assets:read"],
            "webhook_enabled": True,
            "webhook_url": "http://localhost:27125/notify",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "Renamed webhook client"
    assert edited.json()["scopes"] == ["assets:read", "cards:read"]
    assert edited.json()["webhook_delivery_status"] == "active"
    assert edited.json()["webhook_config_generation"] == 2
    rotated = client.post(f"/api/token-auth/tokens/{token['token_id']}/rotate")
    assert rotated.status_code == 201
    assert rotated.json()["token"]["webhook_enabled"] is True
    assert rotated.json()["token"]["name"] == "Renamed webhook client"
    assert rotated.json()["token"]["scopes"] == ["assets:read", "cards:read"]
    assert rotated.json()["token"]["webhook_url"] == "http://localhost:27125/notify"
    assert rotated.json()["token"]["webhook_delivery_status"] == "pending_verification"


def test_webhook_backfill_setting_defaults_off_and_survives_rotation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.application.usecases.api_tokens._probe_or_raise",
        lambda *_args, **_kwargs: 204,
    )
    client = TestClient(create_app(tmp_path))
    created = client.post("/api/token-auth/tokens", json={
        "name": "Webhook", "scopes": ["cards:read"], "webhook_enabled": True,
        "webhook_url": "http://127.0.0.1:27124/hook",
    })
    token = created.json()["token"]
    assert token["webhook_include_backfill"] is False
    edited = client.patch(f"/api/token-auth/tokens/{token['token_id']}", json={
        "webhook_enabled": True, "webhook_url": "http://127.0.0.1:27124/hook",
        "webhook_include_backfill": True,
    })
    assert edited.status_code == 200
    assert edited.json()["webhook_include_backfill"] is True
    assert client.get("/api/token-auth/tokens").json()["items"][0]["webhook_include_backfill"] is True
    rotated = client.post(f"/api/token-auth/tokens/{token['token_id']}/rotate")
    assert rotated.json()["token"]["webhook_include_backfill"] is True


def test_backfill_toggle_preserves_paused_webhook_health(tmp_path, monkeypatch) -> None:
    def unexpected_probe(*_args, **_kwargs):
        raise AssertionError("policy-only edit must not probe the endpoint")

    client = TestClient(create_app(tmp_path))
    created = client.post("/api/token-auth/tokens", json={
        "name": "Webhook", "scopes": ["cards:read"], "webhook_enabled": True,
        "webhook_url": "http://127.0.0.1:27124/hook",
    })
    token = created.json()["token"]
    repository = ApiTokenRepository(tmp_path)
    repository.record_webhook_success(token["token_id"], allow_recovery=True)
    for _ in range(3):
        repository.record_webhook_failure(token["token_id"], error="offline")
    monkeypatch.setattr("backend.application.usecases.api_tokens._probe_or_raise", unexpected_probe)
    edited = client.patch(f"/api/token-auth/tokens/{token['token_id']}", json={
        "webhook_enabled": True, "webhook_url": "http://127.0.0.1:27124/hook",
        "webhook_include_backfill": True,
    })
    assert edited.status_code == 200
    assert edited.json()["webhook_delivery_status"] == "paused"
    assert edited.json()["webhook_consecutive_failures"] == 3
    assert edited.json()["webhook_config_generation"] == 2


def test_paused_offline_webhook_allows_metadata_only_api_edit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.application.usecases.api_tokens._probe_or_raise",
        lambda *_args, **_kwargs: 204,
    )
    client = TestClient(create_app(tmp_path))
    created = client.post(
        "/api/token-auth/tokens",
        json={
            "name": "Before",
            "scopes": ["cards:read"],
            "webhook_enabled": True,
            "webhook_url": "http://127.0.0.1:27124/hook",
        },
    ).json()["token"]
    tested = client.post(
        f"/api/token-auth/tokens/{created['token_id']}/webhook-test"
    )
    assert tested.status_code == 200

    repository = ApiTokenRepository(tmp_path)
    for index in range(3):
        repository.record_webhook_failure(
            created["token_id"],
            error=f"offline-{index}",
            attempted_at=f"2026-07-20T00:0{index}:00Z",
        )
    before = repository.get_internal(created["token_id"])
    monkeypatch.setattr(
        "backend.application.usecases.api_tokens._probe_or_raise",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not probe")),
    )

    edited = client.patch(
        f"/api/token-auth/tokens/{created['token_id']}",
        json={
            "name": "After",
            "scopes": ["cards:read", "assets:read"],
            "webhook_enabled": True,
            "webhook_url": "http://127.0.0.1:27124/hook",
        },
    )

    assert edited.status_code == 200
    assert edited.json()["name"] == "After"
    assert edited.json()["scopes"] == ["assets:read", "cards:read"]
    after = repository.get_internal(created["token_id"])
    assert after["webhook_config_generation"] == before["webhook_config_generation"]
    assert after["webhook_delivery_status"] == "paused"
    assert after["webhook_consecutive_failures"] == 3
    assert after["webhook_last_error"] == before["webhook_last_error"]


def test_token_webhook_rejects_unsafe_urls_with_flat_error(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))
    for unsafe in (
        "http://10.0.0.1/hook",
        "https://127.0.0.1/hook",
        "http://user:pass@127.0.0.1/hook",
        "http://127.0.0.1/hook#fragment",
    ):
        response = client.post(
            "/api/token-auth/tokens",
            json={
                "name": "Unsafe",
                "scopes": ["cards:read"],
                "webhook_enabled": True,
                "webhook_url": unsafe,
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "unsafe_webhook_url"


def test_new_token_webhook_waits_for_a_signed_post_create_verification(
    tmp_path, monkeypatch
) -> None:
    import hmac
    import json
    from hashlib import sha256

    client = TestClient(create_app(tmp_path))
    response = client.post(
        "/api/token-auth/tokens",
        json={
            "name": "Offline receiver",
            "scopes": ["cards:read"],
            "webhook_enabled": True,
            "webhook_url": "http://127.0.0.1:27124/hook",
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["token"]["webhook_delivery_status"] == "pending_verification"
    signing_key = sha256(created["plaintext_token"].encode()).digest()

    def verify_signed_probe(_url, *, body, headers, **_kwargs):
        assert json.loads(body)["event"] == "webhook.test"
        expected = hmac.new(signing_key, body, sha256).hexdigest()
        assert headers["X-InfoOS-Signature"] == f"sha256={expected}"
        return 204

    monkeypatch.setattr(
        "backend.infrastructure.webhooks.delivery.post_webhook",
        verify_signed_probe,
    )
    tested = client.post(
        f"/api/token-auth/tokens/{created['token']['token_id']}/webhook-test"
    )

    assert tested.status_code == 200
    assert tested.json()["token"]["webhook_delivery_status"] == "active"


def test_token_webhook_test_resumes_and_replay_requeues_failed_deliveries(
    tmp_path, monkeypatch
) -> None:
    from backend.application.usecases.api_tokens import create_token

    token = create_token(
        tmp_path,
        name="Replay client",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/hook",
    )["token"]
    token_repo = ApiTokenRepository(tmp_path)
    for index in range(3):
        token_repo.record_webhook_failure(token["token_id"], error="connection refused")
    task_repo = TaskRepository.for_base_dir(tmp_path)
    task_repo.enqueue(Task(
        task_id="webhook_replay_me",
        task_type=TaskType.WEBHOOK_DELIVER,
        target_kind="card",
        target_id="card_replay",
        status=TaskStatus.FAILED,
        artifacts_summary={"token_id": token["token_id"]},
    ))
    monkeypatch.setattr(
        "backend.application.usecases.api_tokens._probe_or_raise",
        lambda *_args, **_kwargs: 204,
    )
    client = TestClient(create_app(tmp_path))

    tested = client.post(f"/api/token-auth/tokens/{token['token_id']}/webhook-test")
    replayed = client.post(
        f"/api/token-auth/tokens/{token['token_id']}/webhook-replay",
        json={"limit": 10},
    )

    assert tested.status_code == 200
    assert tested.json()["token"]["webhook_delivery_status"] == "active"
    assert tested.json()["token"]["webhook_consecutive_failures"] == 0
    assert tested.json()["token"]["webhook_config_generation"] == 2
    assert replayed.status_code == 200
    assert replayed.json()["replayed_count"] == 1
    replay_task = task_repo.get("webhook_replay_me")
    assert replay_task.status is TaskStatus.PENDING
    assert replay_task.artifacts_summary["webhook_config_generation"] == 2
