from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DIR = PROJECT_ROOT / "deploy" / "docker"
RUNTIME_CAPABILITY_CHECK = (
    DOCKER_DIR
    / "rootfs"
    / "opt"
    / "infoos"
    / "bin"
    / "infoos-verify-runtime-capabilities"
)


def _compose() -> dict:
    return yaml.safe_load((DOCKER_DIR / "compose.yaml").read_text())


def test_compose_has_one_service_and_one_published_gateway_port():
    compose = _compose()

    assert set(compose["services"]) == {"infoos"}
    service = compose["services"]["infoos"]
    assert "network_mode" not in service
    assert service["security_opt"] == ["seccomp:chrome-seccomp.json"]
    assert service["ports"] == [
        "${INFOOS_BIND_ADDRESS:-127.0.0.1}:${INFOOS_HTTP_PORT:-8443}:5800"
    ]
    assert all(forbidden not in service["ports"][0] for forbidden in ("5900", "9222", "19825"))


def test_chrome_seccomp_profile_only_extends_namespace_syscalls():
    profile = yaml.safe_load((DOCKER_DIR / "chrome-seccomp.json").read_text())

    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    namespace_rules = [
        rule
        for rule in profile["syscalls"]
        if rule.get("comment") == "Allow create user namespaces"
    ]
    assert len(namespace_rules) == 1
    assert set(namespace_rules[0]["names"]) == {"clone", "setns", "unshare"}


def test_compose_persists_state_and_data_without_embedding_credentials():
    service = _compose()["services"]["infoos"]

    assert "infoos-state:/var/lib/infoos/state" in service["volumes"]
    assert "infoos-state:/config" in service["volumes"]
    assert "infoos-data:/var/lib/infoos/data" in service["volumes"]
    environment = service["environment"]
    assert environment["SECURE_CONNECTION"] == "1"
    assert environment["WEB_AUTHENTICATION"] == "1"
    assert str(environment["WEB_AUTHENTICATION_USERNAME"]).startswith("${INFOOS_ADMIN_USERNAME:-")
    assert str(environment["WEB_AUTHENTICATION_PASSWORD"]).startswith("${INFOOS_ADMIN_PASSWORD:-")


def test_image_keeps_chrome_sandbox_and_dedicated_profile():
    dockerfile = (DOCKER_DIR / "Dockerfile").read_text()
    compose_text = (DOCKER_DIR / "compose.yaml").read_text()
    startapp = (DOCKER_DIR / "rootfs" / "startapp.sh").read_text()

    assert "--no-sandbox" not in dockerfile
    assert "--no-sandbox" not in compose_text
    assert "--no-sandbox" not in startapp
    assert "--user-data-dir=\"$profile_dir\"" in startapp


def test_embedded_proxy_is_loopback_only_and_fails_closed_on_proxy_conflicts():
    init = (
        DOCKER_DIR
        / "rootfs"
        / "etc"
        / "cont-init.d"
        / "20-infoos-runtime.sh"
    ).read_text()
    proxy_run = (
        DOCKER_DIR
        / "rootfs"
        / "etc"
        / "services.d"
        / "infoos-proxy"
        / "run"
    ).read_text()

    assert "http://127.0.0.1:" in init
    assert "must equal the embedded proxy loopback URL" in init
    assert "-ext-ctl \"127.0.0.1:${control_port}\"" in proxy_run
    assert "NET_ADMIN" not in (DOCKER_DIR / "compose.yaml").read_text()


def test_embedded_proxy_config_writer_preserves_routes_and_removes_listeners(
    tmp_path: Path,
):
    source = tmp_path / "source.yaml"
    runtime_dir = tmp_path / "runtime"
    source.write_text(
        """
mixed-port: 7897
allow-lan: true
external-controller: 0.0.0.0:9090
secret: top-secret
tun:
  enable: true
dns:
  enable: true
  listen: 0.0.0.0:53
  nameserver:
    - 223.5.5.5
proxies:
  - name: preserved
    type: ss
    server: example.com
    port: 443
    password: proxy-secret
proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - preserved
rules:
  - MATCH,PROXY
""".lstrip()
    )
    writer = (
        DOCKER_DIR
        / "rootfs"
        / "opt"
        / "infoos"
        / "bin"
        / "infoos-prepare-embedded-proxy-config"
    )
    result = subprocess.run(
        [sys.executable, str(writer)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "INFOOS_EMBEDDED_PROXY_CONFIG": str(source),
            "INFOOS_EMBEDDED_PROXY_RUNTIME_DIR": str(runtime_dir),
            "INFOOS_EMBEDDED_PROXY_MIXED_PORT": "17897",
        },
    )

    assert result.returncode == 0, result.stderr
    runtime = yaml.safe_load((runtime_dir / "config.yaml").read_text())
    assert runtime["mixed-port"] == 17897
    assert runtime["allow-lan"] is False
    assert runtime["bind-address"] == "127.0.0.1"
    assert runtime["listeners"] == []
    assert runtime["tun"] == {"enable": False}
    assert "listen" not in runtime["dns"]
    assert runtime["proxies"][0]["password"] == "proxy-secret"
    assert runtime["rules"] == ["MATCH,PROXY"]
    assert "external-controller" not in runtime
    assert "secret" not in runtime


@pytest.mark.parametrize(
    "revision",
    ["", "unknown", "abc123", "g" * 40, "0" * 40, "0" * 64],
)
def test_source_revision_validator_rejects_untraceable_values(revision: str):
    validator = DOCKER_DIR / "scripts" / "validate-source-revision.sh"

    result = subprocess.run(
        [str(validator), revision],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "INFOOS_SOURCE_COMMIT" in result.stderr


@pytest.mark.parametrize("revision", ["a" * 40, "0123456789abcdef" * 4])
def test_source_revision_validator_accepts_full_git_object_ids(revision: str):
    validator = DOCKER_DIR / "scripts" / "validate-source-revision.sh"

    result = subprocess.run(
        [str(validator), revision],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == revision


@pytest.mark.parametrize("canonical_branch, branch, expected_status", [
    ("", "release", 0),
    ("main", "main", 0),
    ("main", "feature/review", 65),
])
def test_release_build_wrapper_derives_revision_from_clean_checkout(
    tmp_path: Path,
    canonical_branch: str,
    branch: str,
    expected_status: int,
):
    docker_dir = tmp_path / "deploy" / "docker"
    scripts_dir = docker_dir / "scripts"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    for relative in ("build-image.sh", "scripts/validate-source-revision.sh"):
        source = DOCKER_DIR / relative
        destination = docker_dir / relative
        shutil.copy2(source, destination)
        destination.chmod(0o755)
    (docker_dir / ".env").write_text("INFOOS_ADMIN_USERNAME=test\n")
    (docker_dir / "compose.yaml").write_text("services: {}\n")
    revision = "b" * 40
    git = fake_bin / "git"
    git.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *ls-files*) exit 0 ;;\n"
        "  *rev-parse*) printf '%s\\n' \"$FAKE_REVISION\" ;;\n"
        "  *branch*) printf '%s\\n' \"$FAKE_BRANCH\" ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    git.chmod(0o755)
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf 'revision=%s\\nargs=%s\\n' \"$INFOOS_SOURCE_COMMIT\" \"$*\"\n"
    )
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_REVISION": revision,
        "FAKE_BRANCH": branch,
        "INFOOS_CANONICAL_BRANCH": canonical_branch,
    }

    result = subprocess.run(
        [str(docker_dir / "build-image.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == expected_status, result.stderr
    if expected_status == 0:
        assert f"revision={revision}" in result.stdout
        assert "compose --env-file" in result.stdout
        assert result.stdout.rstrip().endswith("build")
    else:
        assert "release images must be built from main" in result.stderr
        assert "revision=" not in result.stdout


def _run_cuda_capability_check(
    tmp_path: Path,
    *,
    image_capability: str,
    provider_available: bool = True,
    gpu_available: bool = True,
) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "cuda-asr"
    marker.write_text(f"{image_capability}\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python = fake_bin / "python"
    python.write_text(f"#!/bin/sh\nexit {0 if provider_available else 1}\n")
    python.chmod(0o755)
    nvidia_smi = fake_bin / "nvidia-smi"
    nvidia_smi.write_text(f"#!/bin/sh\nexit {0 if gpu_available else 1}\n")
    nvidia_smi.chmod(0o755)
    return subprocess.run(
        [str(RUNTIME_CAPABILITY_CHECK)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "INFOOS_FASTER_WHISPER_CUDA_ENABLED": "1",
            "INFOOS_CUDA_ASR_CAPABILITY_FILE": str(marker),
            "INFOOS_PYTHON_EXECUTABLE": str(python),
        },
    )


def test_cuda_runtime_capability_rejects_cpu_image_when_cuda_is_enabled(tmp_path: Path):
    result = _run_cuda_capability_check(tmp_path, image_capability="0")

    assert result.returncode != 0
    assert "image was built without CUDA ASR" in result.stderr


def test_cuda_runtime_capability_rejects_missing_provider_or_gpu(tmp_path: Path):
    missing_provider = _run_cuda_capability_check(
        tmp_path / "provider",
        image_capability="1",
        provider_available=False,
    )
    missing_gpu = _run_cuda_capability_check(
        tmp_path / "gpu",
        image_capability="1",
        gpu_available=False,
    )

    assert missing_provider.returncode != 0
    assert "provider unavailable" in missing_provider.stderr
    assert missing_gpu.returncode != 0
    assert "NVIDIA GPU unavailable" in missing_gpu.stderr


def test_cuda_runtime_capability_accepts_matching_image_provider_and_gpu(tmp_path: Path):
    result = _run_cuda_capability_check(tmp_path, image_capability="1")

    assert result.returncode == 0, result.stderr


def test_gateway_owns_browser_console_and_infoos_proxy_paths():
    config = (DOCKER_DIR / "rootfs" / "opt" / "base" / "etc" / "nginx" / "default_site.conf").read_text()

    assert "include /var/tmp/nginx/auth[.]conf;" in config
    assert config.index("error_page 401 = @infoos_login_home;") < config.index(
        "include /var/tmp/nginx/auth[.]conf;"
    )
    login_home_location = config.split("location @infoos_login_home {", 1)[1].split("\n    }", 1)[0]
    assert 'add_header Set-Cookie "login_success_url=/;Path=/";' in login_home_location
    assert "return 302 /login/;" in login_home_location
    assert "location = /favicon.ico {" in config
    favicon_location = config.split("location = /favicon.ico {", 1)[1].split("\n    }", 1)[0]
    assert "auth_request off;" in favicon_location
    assert "return 204;" in favicon_location
    assert "location ^~ /api/plugin/v1/" in config
    plugin_location = config.split("location ^~ /api/plugin/v1/", 1)[1].split(
        "\n    }", 1
    )[0]
    assert "auth_request off;" in plugin_location
    assert "access_log off;" in plugin_location
    assert "if ($request_method" not in plugin_location
    assert "proxy_pass http://127.0.0.1:8000;" in plugin_location
    assert "location ^~ /api/ {" not in config
    assert "location /browser-console/" in config
    assert "location = /browser-console/websockify" in config
    assert "location = /browser-console/heartbeat" in config
    assert config.count("auth_request /_infoos_browser_console_auth;") == 1
    assert "X-InfoOS-Console-Grant $cookie_infoos_browser_console" in config
    assert "proxy_pass http://127.0.0.1:8000/api/browser/console-websocket;" in config
    assert "websockify_pass unix:/tmp/vnc.sock;" not in config
    assert "root /opt/noVNC;" in config
    assert "proxy_pass http://127.0.0.1:8000;" in config


def test_web_service_binds_only_to_loopback():
    services = DOCKER_DIR / "rootfs" / "etc" / "services.d"

    assert "127.0.0.1" in (services / "infoos-web" / "run").read_text()


def test_reader_locations_use_internal_auth_and_reject_mutations():
    import re
    config = (DOCKER_DIR / "rootfs/opt/base/etc/nginx/default_site.conf").read_text()
    for location in ("^~ /r/", "^~ /assets/",
                     "~ ^/api/card-preview(?:/[^/]+)?$", "~ ^/api/card-preview/assets/[^/]+(?:/thumbnail)?$",
                     "~ ^/api/intelligence/(?:daily-brief/[^/]+|knowledge(?:/[^/]+)?)$"):
        block = config.split(f"location {location} {{", 1)[1].split("\n    }", 1)[0]
        assert "auth_request /_infoos_reader_auth;" in block
        # The base image injects allow/deny into brace blocks at start; method limits
        # live in the reader auth endpoint, so no limit_except/deny here.
        assert "limit_except" not in block and "deny" not in block
        assert 'proxy_set_header X-InfoOS-Edge-Auth "";' in block
        assert not re.search(r"\bif\s*\(", block)
    auth = config.split("location = /_infoos_reader_auth {", 1)[1].split("\n    }", 1)[0]
    assert "internal;" in auth and "access_log off;" in auth
    assert "proxy_pass_request_body off;" in auth
    assert "X-Original-URI $request_uri" in auth
    assert "X-Original-Method $request_method" in auth
    assert "X-InfoOS-Edge-Auth $http_x_infoos_edge_auth" in auth
    direct = config.split("location = /api/internal/reader-authorize {", 1)[1].split("\n    }", 1)[0]
    assert "return 404;" in direct
