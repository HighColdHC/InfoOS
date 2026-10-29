from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.security import (
    SameOriginManagementMiddleware,
    is_same_origin_exempt_path,
)
from backend.app.main import create_app


SAME_ORIGIN = {
    "origin": "http://testserver",
    "sec-fetch-site": "same-origin",
}


def _guarded_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SameOriginManagementMiddleware)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def endpoint(path: str) -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def _without_origin(client: TestClient, method: str, path: str):
    return client.request(
        method,
        path,
        headers={"origin": "", "sec-fetch-site": "", "referer": ""},
    )


def test_exclusion_inventory_is_exact_and_prefix_bounded() -> None:
    for path in (
        "/healthz",
        "/api/capture/v1",
        "/api/capture/v1/items",
        "/api/plugin/v1",
        "/api/plugin/v1/cards",
        "/mcp",
        "/mcp/",
    ):
        assert is_same_origin_exempt_path(path)

    for path in (
        "/healthz/deep",
        "/api/capture/v10/items",
        "/api/capture/v1-malicious/items",
        "/api/plugin/v10/cards",
        "/api/plugin/v1-malicious/cards",
        "/mcp-malicious",
    ):
        assert not is_same_origin_exempt_path(path)


def test_all_management_mutation_methods_require_same_origin() -> None:
    client = _guarded_client()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        missing = _without_origin(client, method, "/api/manage")
        assert missing.status_code == 403
        assert missing.json() == {"detail": "same-origin request required"}

        cross_site = client.request(
            method,
            "/api/manage",
            headers={"origin": "https://attacker.example", "sec-fetch-site": "cross-site"},
        )
        assert cross_site.status_code == 403

        same_site = client.request(method, "/api/manage", headers=SAME_ORIGIN)
        assert same_site.status_code == 200


def test_safe_methods_and_machine_interfaces_are_exempt() -> None:
    client = _guarded_client()
    assert _without_origin(client, "GET", "/api/manage").status_code == 200
    assert _without_origin(client, "GET", "/healthz").status_code == 200
    assert _without_origin(client, "POST", "/api/capture/v1/items").status_code == 200
    assert _without_origin(client, "POST", "/api/plugin/v1/future-command").status_code == 200


def test_common_management_posts_accept_same_origin_and_reject_others(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))
    cases = (
        "/api/configurable-sources/missing/run",
        "/api/token-auth/tokens/missing/revoke",
        "/api/setup/dismiss",
    )
    for path in cases:
        assert client.post(path, headers=SAME_ORIGIN).status_code != 403
        assert _without_origin(client, "POST", path).status_code == 403
        assert client.post(
            path,
            headers={"origin": "https://attacker.example", "sec-fetch-site": "cross-site"},
        ).status_code == 403


def test_bearer_routes_reach_auth_without_origin(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))
    capture = _without_origin(client, "POST", "/api/capture/v1/items")
    plugin = _without_origin(client, "GET", "/api/plugin/v1/cards")
    assert capture.status_code == 401
    assert plugin.status_code == 401
    assert capture.json().get("detail") != "same-origin request required"
    assert plugin.json().get("detail") != "same-origin request required"
