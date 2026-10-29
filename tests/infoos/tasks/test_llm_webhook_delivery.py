from __future__ import annotations

import json
import hmac
from hashlib import sha256
from pathlib import Path

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_tasks_by_ids
from backend.application.usecases.api_tokens import create_token
from backend.application.usecases.api_tokens import replay_failed_webhooks
from backend.application.usecases.api_tokens import revoke_token
from backend.application.usecases.api_tokens import update_token_webhook
from backend.domain.sources.configuration import validate_source_payload
from backend.infrastructure.storage.api_tokens import ApiTokenRepository
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
    SourceRepository,
)
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.webhooks.delivery import post_webhook
from backend.infrastructure.webhooks.delivery import WebhookTransportError
from backend.infrastructure import public_http as infrastructure_public_http


def _webhook_task(token: dict, *, task_id: str) -> Task:
    return Task(
        task_id=task_id,
        task_type=TaskType.WEBHOOK_DELIVER,
        target_kind="card",
        target_id="card-webhook-state",
        artifacts_summary={
            "token_id": token["token_id"],
            "webhook_config_generation": token["webhook_config_generation"],
            "source_id": "source-webhook-state",
            "card_version": 1,
            "content_hash": "sha256:" + "c" * 64,
            "finalized_at": "2026-07-20T00:00:00Z",
        },
    )


def test_finalize_webhook_chain_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_source_and_card(tmp_path)
    created_token = create_token(
        tmp_path,
        name="Obsidian",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/infoos/webhook",
    )
    token = created_token["token"]
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "source_transcript",
            "card_id": "card_delivery",
            "position": 1,
            "layer": "derived",
            "block_kind": "video_transcript",
            "status": "enriched",
            "body": "Spoken transcript",
        }
    )
    delivered: list[dict] = []

    def fake_post(_url: str, *, body: bytes, headers: dict[str, str], **_kwargs) -> int:
        delivered.append({"body": json.loads(body), "headers": headers, "raw": body})
        return 204

    monkeypatch.setattr(
        "backend.application.tasks.handlers.webhook_deliver.post_webhook", fake_post
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    initial = Task(
        task_id="card_finalize_card_delivery_initial",
        task_type=TaskType.CARD_FINALIZE,
        target_kind="card",
        target_id="card_delivery",
        input_version="initial-readable-facts",
    )
    repo.enqueue(initial)

    first = run_tasks_by_ids(tmp_path, [initial.task_id], now="2026-07-20T01:00:00Z")
    assert repo.list_tasks(task_type=TaskType.CARD_LLM_ENRICH) == []
    webhook_id = first["results"][0]["downstream_task_ids"][0]
    assert repo.get(webhook_id).task_type is TaskType.WEBHOOK_DELIVER

    webhook = run_tasks_by_ids(tmp_path, [webhook_id], now="2026-07-20T01:03:00Z")
    assert webhook["succeeded"] == 1
    assert len(delivered) == 1
    payload = delivered[0]["body"]
    assert payload == {
        "event_id": payload["event_id"],
        "event": "card.finalized",
        "card_id": "card_delivery",
        "source_id": "source_delivery",
        "card_version": 1,
        "content_hash": CardRepository(tmp_path).get_card("card_delivery")["content_hash"],
        "finalized_at": CardRepository(tmp_path).get_card("card_delivery")["updated_at"],
        "pull_url": "/api/plugin/v1/cards/card_delivery",
    }
    assert payload["event_id"].startswith("evt_")
    assert token["token_id"] not in json.dumps(payload)
    assert set(delivered[0]["headers"]) == {
        "Content-Type",
        "X-InfoOS-Event-Id",
        "X-InfoOS-Timestamp",
        "X-InfoOS-Signature",
    }
    assert delivered[0]["headers"]["X-InfoOS-Signature"].startswith("sha256=")
    expected_key = sha256(created_token["plaintext_token"].encode()).digest()
    assert delivered[0]["headers"]["X-InfoOS-Signature"] == (
        "sha256=" + hmac.new(expected_key, delivered[0]["raw"], sha256).hexdigest()
    )

    assert not any(b["layer"] == "intelligence" for b in CardContentBlockRepository(tmp_path).list_for_card("card_delivery"))
    assert run_tasks_by_ids(tmp_path, [initial.task_id])["processed"] == 0
    assert len(repo.list_tasks(task_type=TaskType.WEBHOOK_DELIVER)) == 1


def test_source_webhook_does_not_wait_for_legacy_llm_readiness(
    tmp_path: Path,
) -> None:
    _seed_source_and_card(tmp_path)
    source_repo = SourceRepository(tmp_path)
    source = source_repo.get_source("source_delivery")
    assert source is not None
    source["raw_metadata"]["ai_processing"]["enabled"] = False
    source_repo.upsert_source(source)
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "video-without-transcript",
            "card_id": "card_delivery",
            "asset_kind": "video",
            "source_url": "https://example.com/video.mp4",
            "status": "downloaded",
        }
    )
    create_token(
        tmp_path,
        name="Obsidian",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/infoos/webhook",
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    finalize = Task(
        task_id="non-ai-finalize",
        task_type=TaskType.CARD_FINALIZE,
        target_kind="card",
        target_id="card_delivery",
    )
    repo.enqueue(finalize)

    result = run_tasks_by_ids(tmp_path, [finalize.task_id])

    downstream = result["results"][0]["downstream_task_ids"]
    assert len(downstream) == 1
    assert repo.get(downstream[0]).task_type is TaskType.WEBHOOK_DELIVER


def test_finalize_retry_repairs_delivery_after_card_commit_crash_window(
    tmp_path: Path,
) -> None:
    from backend.application.tasks.handlers.card_finalize import handle_card_finalize

    _seed_source_and_card(tmp_path)
    _set_ai_enabled(tmp_path, False)
    create_token(
        tmp_path,
        name="Crash repair client",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/hook",
    )
    task = Task(
        task_id="finalize_crash_window",
        task_type=TaskType.CARD_FINALIZE,
        target_kind="card",
        target_id="card_delivery",
        input_version="crash-window-v1",
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)

    first_handler_result = handle_card_finalize(task, tmp_path)
    assert first_handler_result["artifacts"]["changed"] is True
    assert repo.get(task.task_id).status is TaskStatus.PENDING

    recovered = run_tasks_by_ids(tmp_path, [task.task_id])
    assert recovered["results"][0]["status"] == TaskStatus.SUCCESS.value
    assert recovered["results"][0]["artifacts"]["changed"] is False
    child_id = recovered["results"][0]["downstream_task_ids"][0]
    assert repo.get(child_id).task_type is TaskType.WEBHOOK_DELIVER


def test_legacy_ai_source_finalizes_directly_to_webhook(
    tmp_path: Path,
) -> None:
    _seed_source_and_card(tmp_path)
    create_token(
        tmp_path,
        name="Binding disabled client",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/hook",
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    finalize = Task(
        task_id="finalize_before_binding_disabled",
        task_type=TaskType.CARD_FINALIZE,
        target_kind="card",
        target_id="card_delivery",
        input_version="binding-disable-v1",
    )
    repo.enqueue(finalize)
    finalized = run_tasks_by_ids(tmp_path, [finalize.task_id])
    child_id = finalized["results"][0]["downstream_task_ids"][0]
    assert repo.get(child_id).task_type == TaskType.WEBHOOK_DELIVER
    assert repo.list_tasks(task_type=TaskType.CARD_LLM_ENRICH) == []


def test_old_webhook_result_cannot_mutate_a_new_configuration_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = create_token(
        tmp_path,
        name="Generation client",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/old",
    )["token"]
    task = Task(
        task_id="webhook_old_generation",
        task_type=TaskType.WEBHOOK_DELIVER,
        target_kind="card",
        target_id="card-generation",
        artifacts_summary={
            "token_id": token["token_id"],
            "webhook_config_generation": token["webhook_config_generation"],
            "source_id": "source-generation",
            "card_version": 1,
            "content_hash": "sha256:" + "a" * 64,
            "finalized_at": "2026-07-20T00:00:00Z",
        },
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)

    def old_transport_finishes_after_patch(*_args, **_kwargs):
        update_token_webhook(
            tmp_path,
            token["token_id"],
            webhook_enabled=True,
            webhook_url="http://127.0.0.1:27125/new",
        )
        raise WebhookTransportError("old receiver failed")

    monkeypatch.setattr(
        "backend.application.tasks.handlers.webhook_deliver.post_webhook",
        old_transport_finishes_after_patch,
    )
    result = run_tasks_by_ids(tmp_path, [task.task_id])
    current = ApiTokenRepository(tmp_path).get_internal(token["token_id"])

    assert result["results"][0]["status"] == TaskStatus.SKIPPED.value
    assert current["webhook_config_generation"] == token["webhook_config_generation"] + 1
    assert current["webhook_url"].endswith("/new")
    assert current["webhook_delivery_status"] == "active"
    assert current["webhook_consecutive_failures"] == 0


def test_pending_webhook_task_never_posts_or_self_activates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = create_token(
        tmp_path,
        name="Pending client",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/hook",
        verify_webhook=True,
    )["token"]
    task = _webhook_task(token, task_id="webhook_pending")
    TaskRepository.for_base_dir(tmp_path).enqueue(task)
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.application.tasks.handlers.webhook_deliver.post_webhook",
        lambda *_args, **_kwargs: calls.append("posted") or 204,
    )

    result = run_tasks_by_ids(tmp_path, [task.task_id])
    current = ApiTokenRepository(tmp_path).get_internal(token["token_id"])

    assert result["results"][0]["status"] == TaskStatus.SKIPPED.value
    assert calls == []
    assert current["webhook_delivery_status"] == "pending_verification"


def test_inflight_success_cannot_reopen_a_paused_webhook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = create_token(
        tmp_path,
        name="Paused client",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/hook",
    )["token"]
    task = _webhook_task(token, task_id="webhook_inflight")
    TaskRepository.for_base_dir(tmp_path).enqueue(task)
    token_repo = ApiTokenRepository(tmp_path)

    def pause_before_old_success(*_args, **_kwargs):
        for _ in range(3):
            token_repo.record_webhook_failure(
                token["token_id"],
                error="newer request failed",
                expected_generation=token["webhook_config_generation"],
            )
        return 204

    monkeypatch.setattr(
        "backend.application.tasks.handlers.webhook_deliver.post_webhook",
        pause_before_old_success,
    )
    result = run_tasks_by_ids(tmp_path, [task.task_id])
    current = token_repo.get_internal(token["token_id"])

    assert result["results"][0]["status"] == TaskStatus.SKIPPED.value
    assert current["webhook_delivery_status"] == "paused"
    assert current["webhook_consecutive_failures"] == 3


def test_replay_rebinds_failed_delivery_to_the_current_url_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = create_token(
        tmp_path,
        name="Replay generation",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/old",
    )["token"]
    task = _webhook_task(token, task_id="webhook_rebind_generation")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)
    repo.update_status(task.task_id, TaskStatus.FAILED)
    updated = update_token_webhook(
        tmp_path,
        token["token_id"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27125/new",
    )

    replayed = replay_failed_webhooks(tmp_path, token["token_id"])
    rebound = repo.get(task.task_id)
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.application.tasks.handlers.webhook_deliver.post_webhook",
        lambda url, **_kwargs: calls.append(url) or 204,
    )
    result = run_tasks_by_ids(tmp_path, [task.task_id])

    assert replayed["replayed_count"] == 1
    assert rebound.artifacts_summary["webhook_config_generation"] == updated["webhook_config_generation"]
    assert result["results"][0]["status"] == TaskStatus.SUCCESS.value
    assert calls == ["http://127.0.0.1:27125/new"]


def test_webhook_retry_classification_and_execution_time_token_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = create_token(
        tmp_path,
        name="Retry client",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/hook",
    )["token"]
    repo = TaskRepository.for_base_dir(tmp_path)

    def webhook_task(task_id: str) -> Task:
        return Task(
            task_id=task_id,
            task_type=TaskType.WEBHOOK_DELIVER,
            target_kind="card",
            target_id="card-retry",
            max_attempts=4,
            artifacts_summary={
                "token_id": token["token_id"],
                "source_id": "source-retry",
                "card_version": 1,
                "content_hash": "sha256:" + "b" * 64,
                "finalized_at": "2026-07-20T00:00:00Z",
            },
        )

    retry = webhook_task("webhook-retry")
    repo.enqueue(retry)
    monkeypatch.setattr(
        "backend.application.tasks.handlers.webhook_deliver.post_webhook",
        lambda *_args, **_kwargs: 429,
    )
    result = run_tasks_by_ids(tmp_path, [retry.task_id], now="2026-07-20T01:00:00Z")
    assert result["results"][0]["status"] == TaskStatus.PENDING.value
    assert repo.get(retry.task_id).attempt == 2

    blocked = webhook_task("webhook-blocked")
    repo.enqueue(blocked)
    monkeypatch.setattr(
        "backend.application.tasks.handlers.webhook_deliver.post_webhook",
        lambda *_args, **_kwargs: 400,
    )
    result = run_tasks_by_ids(tmp_path, [blocked.task_id], now="2026-07-20T01:01:00Z")
    assert result["results"][0]["status"] == TaskStatus.BLOCKED.value
    assert repo.get(blocked.task_id).attempt == 1

    revoked = webhook_task("webhook-revoked")
    repo.enqueue(revoked)
    revoke_token(tmp_path, token["token_id"])
    result = run_tasks_by_ids(tmp_path, [revoked.task_id], now="2026-07-20T01:02:00Z")
    assert result["results"][0]["status"] == TaskStatus.SKIPPED.value


def test_public_webhook_posts_through_the_connection_level_pinned_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Response:
        status_code = 204

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(
        "backend.infrastructure.webhooks.delivery.validate_webhook_url",
        lambda value: value,
    )
    monkeypatch.setattr(
        "backend.infrastructure.webhooks.delivery.select_supported_proxy",
        lambda _url: "http://proxy.example:8080",
    )
    def fake_pin(*_args, **kwargs):
        captured["pin_kwargs"] = kwargs
        return "198.18.0.8"

    monkeypatch.setattr(
        "backend.infrastructure.webhooks.delivery.validate_and_pin_public_url",
        fake_pin,
    )

    def fake_open(url: str, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("backend.infrastructure.webhooks.delivery.open_pinned_url", fake_open)

    status = post_webhook(
        "https://hooks.example/infoos",
        body=b'{"event":"card.finalized"}',
        headers={"Content-Type": "application/json"},
    )

    assert status == 204
    assert captured["pinned_ip"] == "198.18.0.8"
    assert captured["proxy_url"] == "http://proxy.example:8080"
    assert captured["pin_kwargs"]["allow_nonstandard_port"] is True
    assert captured["method"] == "POST"
    assert captured["body"] == b'{"event":"card.finalized"}'
    assert captured["closed"] is True


def test_direct_https_webhook_preserves_nonstandard_port_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Socket:
        def settimeout(self, value):
            captured["read_timeout"] = value

    class Response:
        status = 204
        headers = {}

        def close(self):
            captured["response_closed"] = True

    class Connection:
        def __init__(self, **kwargs):
            captured["connection"] = kwargs
            self.sock = None

        def connect(self):
            self.sock = Socket()

        def request(self, method, target, body, headers):
            captured["request"] = (method, target, body, headers)

        def getresponse(self):
            return Response()

        def close(self):
            captured["connection_closed"] = True

    monkeypatch.setattr(infrastructure_public_http, "_PinnedHTTPSConnection", Connection)
    response = infrastructure_public_http.open_pinned_url(
        "https://hooks.example:8443/infoos",
        pinned_ip="93.184.216.34",
        headers={"Content-Type": "application/json"},
        timeout=(3, 7),
        method="POST",
        body=b"{}",
    )
    response.close()

    assert captured["connection"]["host"] == "hooks.example"
    assert captured["connection"]["port"] == 8443
    assert captured["connection"]["server_hostname"] == "hooks.example"
    assert captured["request"] == (
        "POST",
        "/infoos",
        b"{}",
        {"Content-Type": "application/json", "Host": "hooks.example:8443"},
    )


def test_webhook_circuit_pauses_after_three_consecutive_transport_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = create_token(
        tmp_path,
        name="Circuit client",
        scopes=["cards:read"],
        webhook_enabled=True,
        webhook_url="http://127.0.0.1:27124/hook",
    )["token"]
    monkeypatch.setattr(
        "backend.application.tasks.handlers.webhook_deliver.post_webhook",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            WebhookTransportError("connection refused")
        ),
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    for index in range(3):
        task = Task(
            task_id=f"webhook_circuit_{index}",
            task_type=TaskType.WEBHOOK_DELIVER,
            target_kind="card",
            target_id=f"card_{index}",
            max_attempts=1,
            artifacts_summary={
                "token_id": token["token_id"],
                "card_version": 1,
                "content_hash": "sha256:" + str(index) * 64,
                "finalized_at": "2026-07-20T00:00:00Z",
            },
        )
        repo.enqueue(task)
        run_tasks_by_ids(tmp_path, [task.task_id], now=f"2026-07-20T00:0{index}:00Z")

    state = ApiTokenRepository(tmp_path).get_internal(token["token_id"])
    assert state["webhook_delivery_status"] == "paused"
    assert state["webhook_consecutive_failures"] == 3
    assert repo.get("webhook_circuit_2").status is TaskStatus.BLOCKED


def _seed_source_and_card(base_dir: Path) -> None:
    source = validate_source_payload(
        {
            "source_id": "source_delivery",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Delivery feed",
            "default_domain_id": None,
            "cadence_hours": 24,
            "enabled": True,
            "connector_id": "source_delivery",
            "raw_metadata": {
                "ai_processing": {
                    "enabled": True,
                    "provider_id": "provider_test",
                    "model": "model-test",
                    "prompt": "Translate and summarize",
                    "target_language": "zh-CN",
                }
            },
        }
    )
    SourceRepository(base_dir).save_configuration(source, change_reason="created")
    CardRepository(base_dir).upsert_card(
        {
            "card_id": "card_delivery",
            "source_id": "source_delivery",
            "source_platform": "rss",
            "source_type": "feed",
            "source_url": "https://example.com/post",
            "source_author": "Example",
            "title": "Readable title",
            "content": "Readable body",
            "captured_at": "2026-07-20T00:00:00Z",
            "processed_at": "2026-07-20T00:00:00Z",
            "raw_payload": {},
        }
    )
    CardContentBlockRepository(base_dir).upsert_block(
        {
            "block_id": "source_body",
            "card_id": "card_delivery",
            "position": 0,
            "layer": "source",
            "block_kind": "card_content",
            "status": "enriched",
            "body": "Readable body",
        }
    )


def _set_ai_enabled(base_dir: Path, enabled: bool) -> None:
    repository = SourceRepository(base_dir)
    source = repository.get_source("source_delivery")
    assert source is not None
    source["raw_metadata"]["ai_processing"]["enabled"] = enabled
    repository.upsert_source(source)
