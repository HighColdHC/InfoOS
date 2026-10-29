"""Edits unrelated to dependencies must not invalidate the multi-GB Python/CUDA layers."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import textwrap
import time
import tomllib

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DIR = PROJECT_ROOT / "deploy" / "docker"


def _manifest_module():
    spec = importlib.util.spec_from_file_location(
        "python_deps_manifest", DOCKER_DIR / "scripts" / "python-deps-manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependency_layers_only_see_the_dependency_manifest(dockerfile_stages):
    runtime = dockerfile_stages["runtime"]
    installs = {}
    for index, (instruction, body) in enumerate(runtime):
        if instruction != "RUN":
            continue
        tokens = shlex.shlex(body, posix=True, punctuation_chars=True)
        tokens.whitespace_split = True
        for token in tokens:
            if token in {".[web,media]", ".[asr-cuda]"} or token.startswith("torch=="):
                installs[token.split("==")[0]] = index
    assert set(installs) == {"torch", ".[web,media]", ".[asr-cuda]"}
    last_install = max(installs.values())
    manifest_seen = False
    for instruction, body in runtime[:last_install]:
        tokens = shlex.split(body)
        if instruction == "COPY":
            options = [token for token in tokens if token.startswith("--")]
            paths = [token for token in tokens if not token.startswith("--")]
            # Only dependency inputs may affect the expensive runtime layers.
            if paths == ["/python-deps-pyproject.toml", "./pyproject.toml"]:
                assert "--from=python-deps-manifest" in options
                manifest_seen = True
            elif options == ["--from=opencli-builder"]:
                assert all(path.startswith("/usr/local/") for path in paths)
            else:
                assert paths == ["deploy/docker/python-constraints-linux-amd64.txt", "./"]
                assert not options
        elif instruction == "LABEL":
            keys = [token.split("=", 1)[0] for token in tokens]
            assert not any(key.startswith("io.infoos.opencli-adapter.") for key in keys)
            assert "org.opencontainers.image.revision" not in keys
        elif instruction == "ARG":
            assert tokens[0].split("=", 1)[0] != "INFOOS_SOURCE_COMMIT"
    assert manifest_seen


def test_manifest_ignores_tool_settings_readme_and_dev_extra():
    module = _manifest_module()
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    baseline = module.render(pyproject)

    pyproject["project"]["readme"] = "CHANGED.md"
    pyproject["project"]["description"] = "changed"
    pyproject.setdefault("tool", {})["ruff"] = {"line-length": 1}
    pyproject["project"]["optional-dependencies"]["dev"] = ["pytest==1"]
    assert module.render(pyproject) == baseline

    rendered = tomllib.loads(baseline)
    assert rendered["project"]["dependencies"] == pyproject["project"]["dependencies"]
    assert "dev" not in rendered["project"]["optional-dependencies"]
    for extra in ("web", "media", "asr-cuda"):
        assert rendered["project"]["optional-dependencies"][extra] == \
            pyproject["project"]["optional-dependencies"][extra]
    assert rendered["tool"]["setuptools"]["packages"]["find"] == \
        pyproject["tool"]["setuptools"]["packages"]["find"]


def test_manifest_changes_when_a_dependency_changes():
    module = _manifest_module()
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    baseline = module.render(pyproject)
    pyproject["project"]["dependencies"].append("example-new-dependency>=1")
    assert module.render(pyproject) != baseline


@pytest.mark.parametrize('have_full_snapshot', [False, True])
def test_storage_cleanup_never_trims_build_cache_by_size(tmp_path, monkeypatch, have_full_snapshot):
    # Execute the real policy with a Docker protocol stub; no daemon is contacted.
    docker = tmp_path / "docker"
    log = tmp_path / "docker.jsonl"
    docker.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
        import json
        import os
        import sys

        args = sys.argv[1:]
        with open(os.environ["DOCKER_CALL_LOG"], "a") as file:
            file.write(json.dumps(args) + "\\n")
        if args in (["info"], ["container", "ls", "--all", "--quiet"],
                    ["image", "ls", "infoos-appliance", "--format", "{{.Repository}}:{{.Tag}}"],
                    ["container", "inspect", "cache-test"], ["system", "df"]):
            pass
        elif args[:3] == ["container", "inspect", "--format"]:
            print({"{{.State.Status}}": "running",
                   "{{.Config.Image}}": "infoos-appliance:test",
                   "{{.Image}}": "sha256:test"}[args[3]])
        elif args == ["image", "inspect", "--format", "{{.Created}}", "sha256:test"]:
            print("2026-10-20T12:00:00Z")
        elif args[:2] != ["builder", "prune"]:
            sys.exit("unexpected Docker call: " + repr(args))
        """))
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_CALL_LOG", str(log))
    backups = tmp_path / 'backups'
    full = backups / '20261023-full'
    selective_old = backups / '20261025-selective'
    selective_new = backups / '20261026-selective'
    for path, format in [(selective_old, 'selective-media-pruned-v1'),
                         (selective_new, 'linked-files-selective-v1'),
                         *([(full, 'linked-files-v2')] if have_full_snapshot else [])]:
        for name in ('assets', 'state', *(['archive'] if format != 'selective-media-pruned-v1' else [])):
            (path / name).mkdir(parents=True)
        (path / 'infoos.sqlite3').touch()
        (path / 'receipt.json').write_text(json.dumps({'backup_format': format}, separators=(',', ':')))
    tar_old = backups / '20261022-tar'
    tar_new = backups / '20261024-tar'
    for path in (tar_old, tar_new):
        path.mkdir()
        (path / 'assets.tar').write_bytes(b'legacy archive')
    monkeypatch.setenv("INFOOS_BACKUP_ROOT", str(backups))
    monkeypatch.setenv("INFOOS_BACKUP_KEEP_SNAPSHOTS", "1")
    monkeypatch.setenv("INFOOS_DOCKER_CURRENT_CONTAINER", "cache-test")
    monkeypatch.setenv("INFOOS_DOCKER_IMAGE_REPOSITORY", "infoos-appliance")
    monkeypatch.delenv("INFOOS_DOCKER_BUILD_CACHE_MARGIN_HOURS", raising=False)
    before = int(time.time())
    subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts/infoos-docker-storage"), "apply"],
        check=True, capture_output=True, text=True,
    )
    after = int(time.time())
    assert selective_new.exists() and not selective_old.exists()
    assert full.exists() == have_full_snapshot
    assert tar_new.exists()
    assert tar_old.exists() != have_full_snapshot
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    prunes = [args for args in calls if args[:2] == ["builder", "prune"]]
    assert len(prunes) == 1
    args = prunes[0][2:]
    assert "--all" in args and "--force" in args
    assert not any(arg.split("=", 1)[0] in {"--max-used-space", "--keep-storage"} for arg in args)
    filters = [args[index + 1] for index, arg in enumerate(args) if arg == "--filter"]
    assert len(filters) == 1 and filters[0].startswith("until=") and filters[0].endswith("s")
    age_seconds = int(filters[0][len("until="):-1])
    cutoff = int(datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp()) - 6 * 3600
    assert before <= cutoff + age_seconds <= after
