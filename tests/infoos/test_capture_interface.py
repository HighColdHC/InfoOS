from __future__ import annotations

from fastapi.testclient import TestClient
from types import SimpleNamespace

from backend.app.main import create_app
from backend.application.tasks.executor import run_tasks_by_ids
from backend.application.tasks.repository import TaskRepository
from backend.application.usecases.api_tokens import create_token
from backend.domain.captures import (
    capture_card_url,
    capture_identity_hash,
    canonical_capture_url,
)
from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository
from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.sources import SourceRepository


def _headers(token: dict, capture_id: str = "client-cap-1") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token['plaintext_token']}",
        "Idempotency-Key": capture_id,
    }


def _payload(capture_id: str = "client-cap-1") -> dict:
    return {
        "schema": "infoos.capture-request.v1",
        "client_capture_id": capture_id,
        "url": "https://example.com/article?utm_source=test",
        "canonical_url": "https://example.com/article",
        "title": "Captured article",
        "selected_text": "specific evidence",
        "page_language": "en",
        "captured_at": "2026-07-30T10:00:00+08:00",
        "capture_mode": "readable_content",
        "rendered_content": {
            "format": "readability_html",
            "html": (
                "<article><h1>Captured article</h1><p onclick='steal()'>"
                "A durable paragraph with enough useful text for the information card."
                "</p><img src='/cover.jpg'><video src='/clip.mp4'></video>"
                "<form><input value='secret'></form><script>bad()</script></article>"
            ),
            "text": "A durable paragraph with enough useful text for the information card.",
        },
        "client": {"name": "save-to-infoos", "version": "0.1.0"},
    }


def test_capture_identity_prefers_platform_item_id_over_url_shape() -> None:
    watch = canonical_capture_url(
        "https://www.youtube.com/watch?v=demoYt00002&utm_source=test"
    )
    short = canonical_capture_url("https://youtu.be/demoYt00002?t=3")
    assert capture_identity_hash("youtube", watch) == capture_identity_hash(
        "youtube", short
    )
    assert capture_card_url("youtube", short) == (
        "https://www.youtube.com/watch?v=demoYt00002"
    )
    assert capture_identity_hash(
        "x", canonical_capture_url("https://x.com/openai/status/123456?ref_src=test")
    ) == capture_identity_hash(
        "x", canonical_capture_url("https://twitter.com/other/status/123456")
    )


def test_capture_scope_capabilities_and_durable_submission(tmp_path, monkeypatch) -> None:
    client = TestClient(create_app(tmp_path))
    capture_token = create_token(tmp_path, name="Capture", scopes=["captures:write"])
    cards_token = create_token(tmp_path, name="Cards", scopes=["cards:read"])
    monkeypatch.setattr("backend.application.usecases.capture_policy.is_public_capture_url", lambda _url: True)

    denied = client.get(
        "/api/capture/v1/capabilities",
        headers={"Authorization": f"Bearer {cards_token['plaintext_token']}"},
    )
    capabilities = client.get(
        "/api/capture/v1/capabilities",
        headers={"Authorization": f"Bearer {capture_token['plaintext_token']}"},
    )
    submitted = client.post(
        "/api/capture/v1/items",
        headers=_headers(capture_token),
        json=_payload(),
    )

    assert denied.status_code == 403
    assert capabilities.status_code == 200
    assert capabilities.json()["accepted_content_encodings"] == ["identity"]
    assert submitted.status_code == 202
    receipt = submitted.json()
    assert receipt["processing_status"] == "pending"
    assert receipt["disposition"] == "new"
    assert receipt["request_id"].startswith("req_")
    internal = client.get(f"/api/card-preview/captures/{receipt['capture_id']}")
    assert internal.status_code == 200
    assert internal.json()["processing_status"] == "pending"

    stored = CaptureReceiptRepository(tmp_path).get(receipt["capture_id"])
    assert stored is not None
    assert stored["task_id"] == receipt["task_id"]
    raw = RawItemRepository(tmp_path).get(receipt["raw_item_id"])
    assert raw is not None
    assert raw["source_url"] == "https://example.com/article"
    rendered = raw["raw_payload"]["raw_metadata"]["sanitized_readable_html"]
    assert raw["raw_payload"]["raw_metadata"]["images"] == [
        "https://example.com/cover.jpg"
    ]
    assert raw["raw_payload"]["raw_metadata"]["videos"] == [
        "https://example.com/clip.mp4"
    ]
    assert "onclick" not in rendered
    assert "<script" not in rendered
    assert "secret" not in rendered
    source = SourceRepository(tmp_path).get_source("browser-inbox::web")
    assert source is not None
    assert source["enabled"] is False
    assert source["raw_metadata"]["system_managed"] is True
    assert client.get("/api/configurable-sources").json()["total"] == 0
    assert client.delete("/api/configurable-sources/browser-inbox::web").status_code == 404
    audit = ApiRequestLogRepository(tmp_path).get_detail(receipt["request_id"])
    assert audit is not None
    assert audit["response_meta"]["capture_id"] == receipt["capture_id"]
    assert audit["response_meta"]["capture_mode"] == "readable_content"
    assert audit["response_meta"]["disposition"] == "new"
    assert audit["response_meta"]["required_scope"] == "captures:write"
    assert audit["response_meta"]["request_bytes"] > 0
    assert "durable paragraph" not in str(audit).lower()
    with ApiRequestLogRepository(tmp_path).connect_existing() as conn:
        stored_audit = conn.execute(
            "select token_id from api_request_logs where request_id = ?",
            (receipt["request_id"],),
        ).fetchone()
    assert stored_audit["token_id"] == capture_token["token"]["token_id"]


def test_capture_idempotency_and_worker_reaches_card_build(tmp_path, monkeypatch) -> None:
    client = TestClient(create_app(tmp_path))
    token = create_token(tmp_path, name="Capture", scopes=["captures:write"])
    monkeypatch.setattr("backend.application.usecases.capture_policy.is_public_capture_url", lambda _url: True)
    first = client.post(
        "/api/capture/v1/items",
        headers=_headers(token),
        json=_payload(),
    )
    replay = client.post(
        "/api/capture/v1/items",
        headers=_headers(token),
        json=_payload(),
    )
    conflict_payload = _payload()
    conflict_payload["title"] = "Changed"
    conflict = client.post(
        "/api/capture/v1/items",
        headers=_headers(token),
        json=conflict_payload,
    )

    assert replay.status_code == 200
    assert replay.json()["capture_id"] == first.json()["capture_id"]
    assert replay.json()["disposition"] == "idempotent_replay"
    assert conflict.status_code == 409

    fetch_result = run_tasks_by_ids(tmp_path, [first.json()["task_id"]], max_workers=1)
    assert fetch_result["succeeded"] == 1
    source_runs = client.get(
        "/api/source-runs",
        params={"query": first.json()["capture_id"]},
    )
    assert source_runs.status_code == 200
    source_run = source_runs.json()["items"][0]
    assert source_run["root_task_id"].startswith("task_")
    assert source_run["source_id"] == "browser-inbox::web"
    source_run_id = source_run["source_run_id"]
    assert client.get(f"/api/source-runs/{source_run_id}/trace").status_code == 200
    card_task_id = fetch_result["results"][0]["downstream_task_ids"][0]
    card_result = run_tasks_by_ids(tmp_path, [card_task_id], max_workers=1)
    assert card_result["succeeded"] == 1
    projected = client.get(
        f"/api/capture/v1/items/{first.json()['capture_id']}",
        headers={"Authorization": f"Bearer {token['plaintext_token']}"},
    )
    assert projected.status_code == 200
    assert projected.json()["processing_status"] == "running"
    assert projected.json()["card_id"].startswith("card_")
    content_task_id = card_result["results"][0]["downstream_task_ids"][0]
    with CaptureReceiptRepository(tmp_path).connect() as conn:
        conn.execute(
            """
            update tasks
            set status = 'failed', error_reason = 'secret upstream detail'
            where task_id = ?
            """,
            (content_task_id,),
        )
    failed = client.get(
        f"/api/capture/v1/items/{first.json()['capture_id']}",
        headers={"Authorization": f"Bearer {token['plaintext_token']}"},
    )
    assert failed.status_code == 200
    assert failed.json()["processing_status"] == "failed"
    assert failed.json()["error_code"] == "capture_processing_failed"
    assert "secret upstream detail" not in str(failed.json())


def test_capture_rejects_compression_private_url_and_cross_token_read(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))
    owner = create_token(tmp_path, name="Owner", scopes=["captures:write"])
    other = create_token(tmp_path, name="Other", scopes=["captures:write"])
    payload = _payload()
    payload["url"] = "http://127.0.0.1/private"
    payload["canonical_url"] = None

    compressed = client.post(
        "/api/capture/v1/items",
        headers=_headers(owner) | {"Content-Encoding": "gzip"},
        content=b"not-gzip",
    )
    private = client.post(
        "/api/capture/v1/items",
        headers=_headers(owner),
        json=payload,
    )

    assert compressed.status_code == 415
    assert private.status_code == 422
    assert private.json()["code"] == "unsafe_capture_url"

    # A valid capture is still owner-bound.
    from backend.application.usecases import capture_policy

    original = capture_policy.is_public_capture_url
    capture_policy.is_public_capture_url = lambda _url: True
    try:
        accepted = client.post(
            "/api/capture/v1/items",
            headers=_headers(owner, "client-owner"),
            json=_payload("client-owner"),
        )
    finally:
        capture_policy.is_public_capture_url = original
    hidden = client.get(
        f"/api/capture/v1/items/{accepted.json()['capture_id']}",
        headers={"Authorization": f"Bearer {other['plaintext_token']}"},
    )
    assert hidden.status_code == 404


def test_url_only_capture_fetches_and_sanitizes_in_worker(tmp_path, monkeypatch) -> None:
    client = TestClient(create_app(tmp_path))
    token = create_token(tmp_path, name="Capture", scopes=["captures:write"])
    monkeypatch.setattr("backend.application.usecases.capture_policy.is_public_capture_url", lambda _url: True)
    monkeypatch.setattr(
        "backend.application.workflows.capture_fetch.acquire_public_document_from_environment",
        lambda *_args, **_kwargs: SimpleNamespace(
            document=SimpleNamespace(
                final_url="https://example.com/final-article",
                content="Fetched title\nFetched body from the worker.",
                readable_html=(
                    "<main><h1>Fetched title</h1>"
                    "<p>Fetched body from the worker.</p>"
                    "<img src=\"https://example.com/cover.jpg\"></main>"
                ),
                images=("https://example.com/cover.jpg",),
                videos=(),
            ),
            provider_evidence={
                "mode": "primary_only",
                "primary": {"provider": "infoos_http"},
            },
        ),
    )
    payload = _payload("client-url-only")
    payload["capture_mode"] = "url_only"
    payload["rendered_content"] = None
    payload["selected_text"] = None

    accepted = client.post(
        "/api/capture/v1/items",
        headers=_headers(token, "client-url-only"),
        json=payload,
    )
    assert accepted.status_code == 202

    result = run_tasks_by_ids(
        tmp_path,
        [accepted.json()["task_id"]],
        max_workers=1,
    )
    assert result["succeeded"] == 1
    raw = RawItemRepository(tmp_path).get(accepted.json()["raw_item_id"])
    assert raw is not None
    assert raw["source_url"] == "https://example.com/final-article"
    assert "Fetched body from the worker." in raw["raw_text"]
    assert "steal()" not in raw["raw_text"]
    metadata = raw["raw_payload"]["raw_metadata"]
    assert "onmouseover" not in metadata["sanitized_readable_html"]
    assert metadata["images"] == ["https://example.com/cover.jpg"]
    fetch_task = TaskRepository.for_base_dir(tmp_path).get(accepted.json()["task_id"])
    assert fetch_task is not None
    assert (
        fetch_task.artifacts_summary["acquisition_checkpoint"][
            "public_document_evidence"
        ]["primary"]["provider"]
        == "infoos_http"
    )
    stored = CaptureReceiptRepository(tmp_path).get(accepted.json()["capture_id"])
    assert stored is not None
    assert stored["canonical_url"] == "https://example.com/final-article"
    receipt = client.get(
        f"/api/capture/v1/items/{accepted.json()['capture_id']}",
        headers={"Authorization": f"Bearer {token['plaintext_token']}"},
    )
    assert receipt.status_code == 200
    assert receipt.json()["processing_status"] == "running"
