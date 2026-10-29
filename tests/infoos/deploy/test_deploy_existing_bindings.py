from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "deploy/docker/deploy-existing.py"
spec = importlib.util.spec_from_file_location("deploy_existing", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rewrite_port_bindings = module.rewrite_port_bindings


def test_optional_archive_snapshot_and_v2_receipt(tmp_path, monkeypatch):
    source = tmp_path / "data" / "archive"
    destination = tmp_path / "backup" / "archive"
    module.sync_optional_archive(source, destination, None)
    assert destination.is_dir()
    calls = []
    source.mkdir(parents=True)
    (source / "task_events").mkdir()
    monkeypatch.setattr(module, "sync_snapshot", lambda *args: calls.append(args))
    module.sync_optional_archive(source, destination, tmp_path / "previous" / "archive")
    assert calls == [(source, destination, None)]

    receipt_dir = tmp_path / "snapshots" / "20260928"
    for name in ("assets", "archive", "state"):
        (receipt_dir / name).mkdir(parents=True)
    (receipt_dir / "infoos.sqlite3").touch()
    volumes = {"/var/lib/infoos/data": "data-volume", "/var/lib/infoos/state": "state-volume"}
    (receipt_dir / "receipt.json").write_text(json.dumps({"backup_format": "linked-files-v2", "volumes": volumes}))
    assert module.latest_linked_backup(receipt_dir.parent, volumes) == receipt_dir


def test_archive_env_only_changes_explicitly_supplied_values():
    key = "INFOOS_TASK_EVENT_HOT_WINDOW_DAYS"
    existing = {key: "45", "WEB_AUTHENTICATION_PASSWORD": "preserved"}
    configured = {name: "30" for name in module.ARCHIVE_ENV_KEYS}
    module.apply_archive_env_overrides(existing, configured, {})
    assert existing[key] == "45"
    module.apply_archive_env_overrides(existing, configured, {key: "30"})
    assert existing[key] == "30"
    assert existing["WEB_AUTHENTICATION_PASSWORD"] == "preserved"


def test_no_addresses_preserve_original_bindings():
    original = {"5800/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15803"}]}

    assert rewrite_port_bindings(original, None) is original


def test_rewrite_product_port_with_same_host_port():
    original = {"5800/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15803"}]}

    updated = rewrite_port_bindings(original, ["127.0.0.1", "192.168.50.10"])

    assert updated == {"5800/tcp": [
        {"HostIp": "127.0.0.1", "HostPort": "15803"},
        {"HostIp": "192.168.50.10", "HostPort": "15803"},
    ]}
    assert original == {"5800/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15803"}]}


@pytest.mark.parametrize("address", ["10.1.2.3", "172.16.0.1", "172.31.255.254"])
def test_accept_other_rfc1918_ranges(address):
    assert rewrite_port_bindings(
        {"5800/tcp": [{"HostPort": "15803"}]}, [address]
    ) == {"5800/tcp": [{"HostIp": address, "HostPort": "15803"}]}


@pytest.mark.parametrize("address", [
    "0.0.0.0", "8.8.8.8", "169.254.1.1", "172.32.0.1", "127.0.0.2", "::1", "localhost",
    "192.168.50.10/24", "192.168.050.010",
])
def test_reject_non_approved_address(address):
    with pytest.raises(ValueError):
        rewrite_port_bindings({"5800/tcp": [{"HostPort": "15803"}]}, [address])


def test_reject_duplicate_address():
    with pytest.raises(ValueError, match="Duplicate"):
        rewrite_port_bindings({"5800/tcp": [{"HostPort": "15803"}]}, ["10.0.0.1", "10.0.0.1"])


def test_reject_extra_published_port():
    with pytest.raises(ValueError, match="Only the product port"):
        rewrite_port_bindings({
            "5800/tcp": [{"HostPort": "15803"}],
            "9222/tcp": [{"HostPort": "9222"}],
        }, ["192.168.50.10"])


def test_reject_ambiguous_existing_host_port():
    with pytest.raises(ValueError, match="one HostPort"):
        rewrite_port_bindings({"5800/tcp": [
            {"HostPort": "15803"}, {"HostPort": "8443"},
        ]}, ["192.168.50.10"])


def test_env_override_file_and_redaction(tmp_path):
    path = tmp_path / "edge.env"
    secret = "secret-" + "a" * 64
    path.write_text(f"# Private operator file\nINFOOS_PUBLIC_BASE_URL=https://infoos.example.com\nINFOOS_EDGE_READER_SECRET={secret}\n")
    path.chmod(0o600)
    overrides = module.read_env_overrides(path)
    assert overrides["INFOOS_EDGE_READER_SECRET"] == secret
    preview = module.preview_env_overrides(overrides)
    assert secret not in json.dumps(preview)
    assert preview == {"INFOOS_PUBLIC_BASE_URL": "https://infoos.example.com", "INFOOS_EDGE_READER_SECRET": "<redacted>"}
    assert module.read_env_overrides(None) == {}


@pytest.mark.parametrize("mode", [0o644, 0o400, 0o660, 0o1600])
def test_env_override_file_rejects_permissions(tmp_path, mode):
    path = tmp_path / "edge.env"
    path.write_text("INFOOS_EDGE_READER_SECRET=private\n")
    path.chmod(mode)
    with pytest.raises(ValueError, match="0600"):
        module.read_env_overrides(path)


@pytest.mark.parametrize("line,match", [
    ("OTHER_KEY=private", "not allowed: OTHER_KEY"),
    ("INFOOS_ADMIN_PASSWORD=private", "not allowed"),
    ("private", "syntax"),
    ("INFOOS_EDGE_READER_SECRET=x\nINFOOS_EDGE_READER_SECRET=y", "Duplicate"),
])
def test_env_override_rejects_other_keys_without_values(tmp_path, line, match):
    path = tmp_path / "edge.env"
    path.write_text(line)
    path.chmod(0o600)
    with pytest.raises(ValueError, match=match) as error:
        module.read_env_overrides(path)
    assert "private" not in str(error.value)


def test_env_override_rejects_wrong_owner(tmp_path, monkeypatch):
    path = tmp_path / "edge.env"
    path.write_text("")
    path.chmod(0o600)
    metadata = path.stat()
    from types import SimpleNamespace
    monkeypatch.setattr(module.os, "fstat", lambda fd: SimpleNamespace(st_mode=metadata.st_mode, st_uid=987654))
    with pytest.raises(ValueError, match="owner"):
        module.read_env_overrides(path)


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("drop", [False, True])
def test_env_override_main_keeps_old_environment_and_redacts_plan(tmp_path, monkeypatch, capsys, apply, drop):
    import sys
    from types import SimpleNamespace
    secret = "new-reader-secret-" + "b" * 40
    old_secret = "old-reader-secret-" + "a" * 40
    path = tmp_path / "edge.env"
    path.write_text(f"INFOOS_EDGE_READER_SECRET={secret}\nINFOOS_PUBLIC_BASE_URL=https://infoos.example.com\n")
    path.chmod(0o600)
    old = {"Config": {"Env": [f"INFOOS_EDGE_READER_SECRET={old_secret}", "UNRELATED=preserved", "WEB_AUTHENTICATION_USERNAME=bootstrap-user",
                            "WEB_AUTHENTICATION_PASSWORD=bootstrap-password", "INFOOS_CLOUD_AI_WORKERS=2",
                            "INFOOS_LLM_CONFIG=/var/lib/infoos/state/config/llm.json"], "Labels": {}},
           "HostConfig": {"PortBindings": {"5800/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15803"}]}},
           "Mounts": [{"Destination": f"/var/lib/infoos/{name}", "Source": str(tmp_path / name),
                       "Name": f"{name}-volume", "Type": "volume", "RW": True} for name in ("data", "state")]}
    image = {"Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}}, "Id": "test-image"}
    monkeypatch.setattr(module, "docker_json", lambda *args: [old if args[0] == "inspect" else image])
    compose = {"stop_grace_period": "30s", "mem_limit": "1024", "mem_reservation": "512",
               "memswap_limit": "2048", "shm_size": "128", "logging": {"driver": "local", "options": {}},
               "environment": {"SERVICES_GRACETIME": "30", "INFOOS_MEMORY_SOFT_LIMIT_MIB": "1",
                               "INFOOS_RESOURCE_MIN_AVAILABLE_MIB": "1"}}
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if "compose" in command:
            return SimpleNamespace(stdout=json.dumps({"services": {"infoos": compose}}))
        return SimpleNamespace(stdout="1.47", stderr="", returncode=0)
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "write_sqlite_recovery_point", lambda *args: None)
    requests = []
    class Connection:
        def request(self, method, url, **kwargs):
            requests.append(json.loads(kwargs["body"]))
        def getresponse(self):
            return SimpleNamespace(status=201, read=lambda: b"{}")
        def close(self):
            pass
    monkeypatch.setattr(module, "DockerConnection", Connection)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--environment", "test", "--image", "test-image",
        "--revision", "a" * 40, "--update-kind", "code-only", "--env-override-file", str(path),
        *( ["--drop-env", "WEB_AUTHENTICATION_USERNAME", "--drop-env", "WEB_AUTHENTICATION_PASSWORD"] if drop else []),
        *( ["--apply"] if apply else [])])
    module.main()
    output = capsys.readouterr().out
    assert secret not in output and old_secret not in output
    plan = json.loads(output.splitlines()[0])
    assert "bootstrap-password" not in output and "bootstrap-user" not in output
    assert plan["env_removed"] == (["INFOOS_CLOUD_AI_WORKERS", "INFOOS_LLM_CONFIG"]
                                   + (["WEB_AUTHENTICATION_PASSWORD", "WEB_AUTHENTICATION_USERNAME"] if drop else []))
    assert plan["nginx_preflight"] == "ok"
    preflight = next(c for c in commands if "nginx -t" in c[-1])
    assert preflight[:5] == ["docker", "run", "--rm", "--network", "none"] and "test-image" in preflight
    assert plan["env_overrides"] == {"INFOOS_EDGE_READER_SECRET": "<redacted>", "INFOOS_PUBLIC_BASE_URL": "https://infoos.example.com"}
    assert secret not in repr(commands)
    if apply:
        env = dict(item.split("=", 1) for item in requests[0]["Env"])
        assert ("WEB_AUTHENTICATION_PASSWORD" not in env) == drop
        assert ("WEB_AUTHENTICATION_USERNAME" not in env) == drop
        assert "INFOOS_CLOUD_AI_WORKERS" not in env and "INFOOS_LLM_CONFIG" not in env
        assert env["UNRELATED"] == "preserved"
        assert env["INFOOS_EDGE_READER_SECRET"] == secret
        assert env["INFOOS_PUBLIC_BASE_URL"] == "https://infoos.example.com"
    else:
        assert not requests


def test_nginx_preflight_passes_only_web_settings_and_stops_before_any_change(monkeypatch):
    from types import SimpleNamespace
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr='nginx: [emerg] "deny" directive is not allowed here')
    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(SystemExit, match="(?s)nginx preflight failed.*deny"):
        module.nginx_preflight("img", {"WEB_AUTHENTICATION": "1", "SECURE_CONNECTION": "1",
                                       "INFOOS_EDGE_READER_SECRET": "s" * 40, "INFOOS_ADMIN_PASSWORD": "p"})
    assert len(commands) == 1
    env = [commands[0][i + 1] for i, part in enumerate(commands[0]) if part == "-e"]
    assert env == ["SECURE_CONNECTION=1", "WEB_AUTHENTICATION=1"]
