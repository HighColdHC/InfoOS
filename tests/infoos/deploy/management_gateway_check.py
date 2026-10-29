"""Opt-in remote acceptance: real nginx, disposable state, no appliance writes.

Run explicitly through infoos-remote-test. Builds only nginx-config-check;
the existing appliance image supplies runtime dependencies to a separate,
volume-free test container whose backend is the current worktree.
"""
import json
from pathlib import Path
import subprocess
import time
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
SECRET = "nginx-management-test-only-" + "x" * 40
OLD = "initial-test-only-password"
NEW = "replacement-test-only-secret"

APP = '''
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from pathlib import Path
from hashlib import sha256
from backend.api.routes import browser, capture, lan_account, management_auth
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.infrastructure.storage.api_tokens import ApiTokenRepository
from backend.application.usecases import capture_policy
from backend.api.security import SameOriginManagementMiddleware
from backend.application.usecases import lan_account as account
from backend.infrastructure.security.lan_passwords import PasswordStore, hash_password

account.reload_and_invalidate_sessions = lambda: None

@asynccontextmanager
async def lifespan(app):
    app.state.base_dir = Path("/tmp/data")
    initialize_runtime_storage(app.state.base_dir)
    for name, scope in (("capture", "captures:write"), ("cards", "cards:read")):
        fake = "nginx-" + name + "-test-only"
        ApiTokenRepository(app.state.base_dir).append(
            token_id=name, name=name, token_prefix=fake[:12],
            token_digest=sha256(fake.encode()).hexdigest(), scopes=[scope])
    capture_policy.is_public_capture_url = lambda url: True
    PasswordStore().write({"tester": hash_password("initial-test-only-password")})
    async def echo(reader, writer):
        writer.write(await reader.read(1024))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_unix_server(echo, "/tmp/vnc.sock")
    async with server:
        yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(SameOriginManagementMiddleware)
for router in (browser.router, capture.router, lan_account.router, management_auth.router):
    app.include_router(router)

@app.middleware("http")
async def capture_headers(request, call_next):
    if request.url.path.startswith("/api/capture/v1/"):
        for name in ("x-infoos-edge-auth", "x-infoos-edge-authenticated", "x-infoos-internal-auth"):
            assert not request.headers.get(name)
    return await call_next(request)

@app.api_route("/api/manage-probe", methods=["GET", "POST"])
def probe(request: Request):
    return {"edge_secret": request.headers.get("x-infoos-edge-auth", ""),
            "edge_authenticated": request.headers.get("x-infoos-edge-authenticated", "")}

@app.post("/api/test-grant")
def grant(response: Response):
    browser._set_console_cookie(response)
    return {"ok": True}

auth = FastAPI()
@auth.get("/auth")
def session(request: Request):
    return Response(status_code=204 if request.cookies.get("auth") == "test-session" else 401)
'''


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    checks = tmp_path_factory.mktemp("management-nginx")
    (checks / "gateway_app.py").write_text(APP)
    name = "infoos-management-check-" + uuid.uuid4().hex[:12]
    image = name + ":nginx"
    container = None
    built = False
    try:
        # No source revision or deployable application image is built here.
        result = subprocess.run([
            "docker", "build", "--target", "nginx-config-check", "-t", image,
            "-f", str(ROOT / "deploy/docker/Dockerfile"), str(ROOT),
        ], text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        built = True
        runtime_image = run("docker", "inspect", "--format", "{{.Image}}", "infoos-acceptance")
        command = '''
set -eu
export PATH=/opt/base/bin:/opt/base/sbin:$PATH
for s in 09-var-dirs.sh 10-init-users.sh 10-certs.sh 10-nginx.sh; do sh /etc/cont-init.d/$s; done
nginx -t -c /opt/base/etc/nginx/nginx.conf
/opt/infoos/venv/bin/python -m uvicorn gateway_app:auth --uds /tmp/webauth.sock --log-level warning &
/opt/infoos/venv/bin/python -m uvicorn gateway_app:app --host 127.0.0.1 --port 8000 --log-level warning &
exec nginx -c /opt/base/etc/nginx/nginx.conf -g 'daemon off;'
'''
        container = run(
            "docker", "run", "-d", "--name", name, "--entrypoint", "sh",
            "--tmpfs", "/config", "--tmpfs", "/tmp", "-p", "127.0.0.1::5800",
            "-v", f"{ROOT / 'backend'}:/opt/infoos/app/backend:ro",
            "-v", f"{checks}:/checks:ro",
            "-v", f"{ROOT / 'deploy/docker/rootfs/opt/base/etc/nginx/default_site.conf'}:/opt/base/etc/nginx/default_site.conf:ro",
            "-w", "/checks", "-e", "PYTHONPATH=/opt/infoos/app",
            "-e", "USER_ID=1000", "-e", "GROUP_ID=1000",
            "-e", "SECURE_CONNECTION=1", "-e", "WEB_AUTHENTICATION=1",
            "-e", "INFOOS_WEBAUTH_PASSWORD_FILE=/tmp/htpasswd",
            "-e", "INFOOS_STATE_HOME=/tmp/state", "-e", "INFOOS_DATA_HOME=/tmp/data",
            "-e", f"INFOOS_EDGE_READER_SECRET={SECRET}", runtime_image, "-c", command,
        )
        binding = json.loads(run("docker", "inspect", "--format", '{{json .NetworkSettings.Ports}}', container))
        port = binding["5800/tcp"][0]["HostPort"]
        with httpx.Client(
            base_url=f"https://127.0.0.1:{port}", verify=False,
            headers={"host": "infoos.example", "origin": "https://infoos.example"},
        ) as client:
            for _ in range(100):
                try:
                    response = client.get("/api/manage-probe", headers={"x-infoos-edge-auth": SECRET})
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail(run("docker", "logs", container))
            yield client, container
    finally:
        if container:
            run("docker", "rm", "-f", container)
        if built:
            run("docker", "image", "rm", image)


def test_nginx_allows_edge_management_api_and_strips_secret(gateway):
    client, _ = gateway
    for method in ("GET", "POST"):
        response = client.request(method, "/api/manage-probe", headers={"x-infoos-edge-auth": SECRET})
        assert response.status_code == 200
        assert response.json() == {"edge_secret": "", "edge_authenticated": "1"}


@pytest.mark.parametrize("secret", ["wrong", ""])
def test_nginx_denies_missing_or_wrong_edge_without_session(gateway, secret):
    client, _ = gateway
    response = client.get("/api/manage-probe", headers={
        "x-infoos-edge-auth": secret, "x-infoos-edge-authenticated": "1",
    })
    assert response.status_code == 302
    assert response.headers["location"] == "/login/"


def test_nginx_retains_form_session_and_login_page(gateway):
    client, _ = gateway
    response = client.get("/api/manage-probe", headers={"cookie": "auth=test-session"})
    assert response.status_code == 200
    assert response.json() == {"edge_secret": "", "edge_authenticated": ""}
    assert client.get("/login/").status_code == 200


@pytest.mark.parametrize("path", ["/api/internal/management-authorize", "/api/internal/reader-authorize",
                                 "/_infoos_management_auth", "/_infoos_reader_auth"])
def test_nginx_internal_endpoints_return_404(gateway, path):
    client, _ = gateway
    assert client.get(path, headers={"x-infoos-edge-auth": SECRET, "x-infoos-internal-auth": "1"}).status_code == 404


def test_nginx_console_accepts_edge_and_keeps_grant_check(gateway):
    client, container = gateway
    headers = {"x-infoos-edge-auth": SECRET, "referer": "https://infoos.example/browser-console/"}
    assert client.get("/browser-console/heartbeat", headers=headers).status_code == 401
    assert client.post("/api/test-grant", headers=headers).status_code == 200
    assert client.get("/browser-console/heartbeat", headers=headers).status_code == 204
    assert client.get("/browser-console/", headers=headers).status_code == 200
    # Use the container's installed WebSocket client to exercise the real bridge.
    script = '''
import asyncio
from websockets.asyncio.client import connect
async def check():
    import ssl
    async with connect("wss://127.0.0.1:5800/browser-console/websockify", ssl=ssl._create_unverified_context(),
                       origin="https://127.0.0.1:5800", additional_headers=HEADERS) as ws:
        await ws.send(b"console-check")
        assert await ws.recv() == b"console-check"
asyncio.run(check())
'''
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    run("docker", "exec", container, "/opt/infoos/venv/bin/python", "-c",
        "HEADERS = " + repr({"x-infoos-edge-auth": SECRET, "cookie": cookie}) + "\n" + script)


def test_nginx_account_edge_auth_still_requires_current_password(gateway):
    client, _ = gateway
    headers = {"x-infoos-edge-auth": SECRET}
    assert client.get("/api/lan-account", headers=headers).json()["username"] == "tester"
    data = {"current_password": "wrong", "new_password": NEW, "confirm_password": NEW}
    assert client.post("/api/lan-account/password", headers=headers, json=data).status_code == 400
    data["current_password"] = OLD
    assert client.post("/api/lan-account/password", headers=headers, json=data).status_code == 200


def test_nginx_capture_bearer_scope_and_management_boundary(gateway):
    client, _ = gateway
    path = "/api/capture/v1/capabilities"
    headers = {"authorization": "Bearer nginx-capture-test-only",
               "x-infoos-edge-auth": SECRET, "x-infoos-edge-authenticated": "1",
               "x-infoos-internal-auth": "1"}
    assert client.get(path, headers={"authorization": headers["authorization"]}).status_code == 200
    assert client.get(path, headers=headers).status_code == 200
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"authorization": "Bearer nginx-cards-test-only"}).status_code == 403
    payload = {"schema": "infoos.capture-request.v1", "client_capture_id": "nginx-capture",
               "url": "https://example.com/article", "title": "Synthetic capture",
               "captured_at": "2026-10-28T00:00:00Z", "capture_mode": "url_only",
               "client": {"name": "gateway-test", "version": "1"}}
    response = client.post("/api/capture/v1/items", json=payload,
                           headers={**headers, "idempotency-key": "nginx-capture"})
    assert response.status_code == 202, response.text
    assert response.json()["processing_status"] == "pending"
    for path in ("/api/manage-probe", "/api/capture/v1evil/capabilities"):
        denied = client.get(path, headers={"authorization": headers["authorization"]})
        assert denied.status_code == 302, (path, denied.status_code, denied.headers.get("location"))
        assert denied.headers["location"] == "/login/"
    assert client.get("/api/capture/v1", headers={"authorization": headers["authorization"]}).status_code == 404
