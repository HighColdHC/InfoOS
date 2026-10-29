from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.api.routes.plugin.router import router
from backend.application.usecases.api_tokens import create_token
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardRepository,
    SourceRepository,
)


def _app(base_dir) -> TestClient:
    app = FastAPI()
    app.state.base_dir = base_dir

    @app.middleware("http")
    async def fixed_request_id(request: Request, call_next):
        request.state.request_id = "req-interface-test"
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _card(
    base_dir,
    card_id: str = "route-card",
    *,
    source_id: str | None = None,
) -> None:
    CardRepository(base_dir).upsert_card(
        {
            "card_id": card_id,
            "card_type": "information",
            "version": 1,
            "content_hash": f"sha256:{'a' * 64}",
            "updated_at": "2026-07-16T01:00:00Z",
            "status": "active",
            "completeness_status": "complete",
            "missing_reasons": [],
            "source_id": source_id,
            "source_platform": "rss",
            "source_type": "article",
            "source_url": f"https://example.test/{card_id}",
            "source_author": "Author",
            "title": "Route card",
            "content": "Route body",
            "published_at": "2026-07-16T00:00:00Z",
            "captured_at": "2026-07-16T00:00:00Z",
            "processed_at": "2026-07-16T00:00:00Z",
        }
    )


def _bearer(token: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {token['plaintext_token']}"}


def _source(base_dir, source_id: str) -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": source_id,
            "platform": "rss",
            "source_type": "feed",
            "identifier": f"https://example.test/{source_id}.xml",
            "display_name": f"Source {source_id}",
            "cadence_hours": 12,
            "enabled": True,
            "connector_status": "supported",
        }
    )


def test_capabilities_health_and_auth_errors_use_stable_contract(tmp_path) -> None:
    client = _app(tmp_path)
    cards = create_token(tmp_path, name="Cards", scopes=["cards:read"])
    all_scopes = create_token(
        tmp_path,
        name="All",
        scopes=["cards:read", "assets:read"],
    )

    missing = client.get("/api/plugin/v1/capabilities")
    capabilities = client.get("/api/plugin/v1/capabilities", headers=_bearer(cards))
    all_capabilities = client.get(
        "/api/plugin/v1/capabilities",
        headers=_bearer(all_scopes),
    )
    health = client.get("/api/plugin/v1/health", headers=_bearer(cards))
    unsupported_method = client.post(
        "/api/plugin/v1/capabilities",
        headers=_bearer(cards),
    )

    assert missing.status_code == 401
    assert missing.json() == {
        "code": "missing_token",
        "message": "Bearer token is required",
        "request_id": "req-interface-test",
    }
    assert capabilities.json() == {
        "interface_version": "v1",
        "card_schema": "infoos.information-card.v1",
        "source_schema": "infoos.source-catalog.v1",
        "capabilities": ["cards:read"],
        "default_page_size": 100,
        "max_page_size": 200,
        "catalog_filters": [
            "query",
            "platform",
            "completeness",
            "media_kind",
            "source_id",
        ],
        "catalog_fields": [
            "completeness_status",
            "excerpt",
            "source_url",
            "asset_summary",
            "source_id",
        ],
        "source_catalog_fields": [
            "source_id",
            "display_name",
            "platform",
            "source_type",
            "card_count",
            "latest_card_updated_at",
        ],
        "web_deep_links": False,
    }
    assert all_capabilities.json()["capabilities"] == ["cards:read", "assets:read"]
    assert health.json() == {"status": "ready", "interface_version": "v1"}
    assert unsupported_method.status_code == 405


def test_catalog_detail_and_validation_errors(tmp_path) -> None:
    client = _app(tmp_path)
    token = create_token(tmp_path, name="Cards", scopes=["cards:read"])
    _card(tmp_path)

    catalog = client.get("/api/plugin/v1/cards", headers=_bearer(token), params={"page_size": 1})
    detail = client.get("/api/plugin/v1/cards/route-card", headers=_bearer(token))
    missing = client.get("/api/plugin/v1/cards/missing", headers=_bearer(token))
    invalid_size = client.get("/api/plugin/v1/cards", headers=_bearer(token), params={"page_size": 0})
    invalid_cursor = client.get(
        "/api/plugin/v1/cards",
        headers=_bearer(token),
        params={"page_token": "broken"},
    )

    assert catalog.status_code == 200
    assert [item["card_id"] for item in catalog.json()["items"]] == ["route-card"]
    assert catalog.json()["items"][0]["completeness_status"] == "complete"
    assert catalog.json()["items"][0]["source_url"] == "https://example.test/route-card"
    assert catalog.json()["items"][0]["asset_summary"]["total_size_bytes"] == 0
    assert client.get(
        "/api/plugin/v1/cards",
        headers=_bearer(token),
        params={"query": "route body", "platform": "rss", "completeness": "complete"},
    ).json()["items"][0]["card_id"] == "route-card"
    assert client.get(
        "/api/plugin/v1/cards",
        headers=_bearer(token),
        params={"media_kind": "video"},
    ).json()["items"] == []
    assert detail.status_code == 200
    assert detail.json()["schema"] == "infoos.information-card.v1"
    assert missing.status_code == 404
    assert missing.json() == {
        "code": "card_not_found",
        "message": "Card not found",
        "request_id": "req-interface-test",
    }
    assert invalid_size.status_code == 422
    assert invalid_size.json()["code"] == "invalid_page_size"
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["code"] == "invalid_page_token"


def test_source_catalog_and_repeated_card_source_filter(tmp_path) -> None:
    client = _app(tmp_path)
    token = create_token(tmp_path, name="Cards", scopes=["cards:read"])
    _source(tmp_path, "source-a")
    _source(tmp_path, "source-b")
    _card(tmp_path, "a-card", source_id="source-a")
    _card(tmp_path, "b-card", source_id="source-b")
    _card(tmp_path, "null-card")

    sources = client.get("/api/plugin/v1/sources", headers=_bearer(token))
    filtered = client.get(
        "/api/plugin/v1/cards",
        headers=_bearer(token),
        params=[("source_id", "source-a"), ("source_id", "source-b")],
    )
    unknown = client.get(
        "/api/plugin/v1/cards",
        headers=_bearer(token),
        params={"source_id": "unknown"},
    )
    invalid_empty = client.get(
        "/api/plugin/v1/cards",
        headers=_bearer(token),
        params={"source_id": ""},
    )
    invalid_source_cursor = client.get(
        "/api/plugin/v1/sources",
        headers=_bearer(token),
        params={"page_token": "broken"},
    )

    assert sources.status_code == 200
    assert sources.json()["schema"] == "infoos.source-catalog.v1"
    assert [item["source_id"] for item in sources.json()["items"]] == [
        "source-a",
        "source-b",
    ]
    assert [item["card_id"] for item in filtered.json()["items"]] == [
        "a-card",
        "b-card",
    ]
    assert unknown.json()["items"] == []
    assert invalid_empty.status_code == 422
    assert invalid_empty.json()["code"] == "invalid_source_id"
    assert invalid_source_cursor.status_code == 422
    assert invalid_source_cursor.json()["code"] == "invalid_page_token"


def test_asset_scope_full_and_range_responses(tmp_path) -> None:
    client = _app(tmp_path)
    cards_only = create_token(tmp_path, name="Cards", scopes=["cards:read"])
    all_scopes = create_token(tmp_path, name="All", scopes=["cards:read", "assets:read"])
    _card(tmp_path, "asset-card")
    target = tmp_path / "data" / "assets" / "asset-card" / "video.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"0123456789")
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "route-asset",
            "card_id": "asset-card",
            "asset_kind": "video",
            "source_url": "https://cdn.test/video",
            "local_path": "asset-card/video.bin",
            "mime_type": "video/mp4",
            "size_bytes": 10,
            "status": "downloaded",
        }
    )

    insufficient = client.get("/api/plugin/v1/assets/route-asset", headers=_bearer(cards_only))
    full = client.get("/api/plugin/v1/assets/route-asset", headers=_bearer(all_scopes))
    ranged = client.get(
        "/api/plugin/v1/assets/route-asset",
        headers={**_bearer(all_scopes), "Range": "bytes=2-5"},
    )
    suffix = client.get(
        "/api/plugin/v1/assets/route-asset",
        headers={**_bearer(all_scopes), "Range": "bytes=-3"},
    )
    invalid = client.get(
        "/api/plugin/v1/assets/route-asset",
        headers={**_bearer(all_scopes), "Range": "bytes=20-30"},
    )

    assert insufficient.status_code == 403
    assert insufficient.json()["code"] == "insufficient_scope"
    assert full.status_code == 200
    assert full.content == b"0123456789"
    assert full.headers["accept-ranges"] == "bytes"
    assert ranged.status_code == 206
    assert ranged.content == b"2345"
    assert ranged.headers["content-range"] == "bytes 2-5/10"
    assert ranged.headers["content-length"] == "4"
    assert suffix.status_code == 206
    assert suffix.content == b"789"
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == "bytes */10"
    assert invalid.json() == {
        "code": "range_not_satisfiable",
        "message": "Requested byte range is not satisfiable",
        "request_id": "req-interface-test",
    }
