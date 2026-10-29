#!/usr/bin/env python3
"""Start an empty local demo, seed via API/MCP, capture, then remove all runtime state."""
from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]


def main():
    if not (ROOT / "frontend/dist/index.html").exists():
        raise SystemExit("Build frontend first: npm --prefix frontend ci && npm --prefix frontend run build")
    children = []
    with tempfile.TemporaryDirectory(prefix="infoos-demo-", dir="/tmp") as directory:
        demo = Path(directory)
        env = {key: value for key, value in os.environ.items() if not key.startswith("INFOOS_")}
        env.update({"INFOOS_STATE_HOME": str(demo / "state"), "INFOOS_DATA_HOME": str(demo / "data"),
                    "INFOOS_HOME": str(ROOT), "INFOOS_WEBAUTH_PASSWORD_FILE": str(demo / "htpasswd"),
                    "INFOOS_CONTAINER_HEALTH_PATH": str(demo / "no-container-health.json"),
                    "INFOOS_CHROME_PROFILE_DIR": str(demo / "unused-chrome-profile"),
                    "INFOOS_CONFIG_PATH": str(demo / "config.yaml")})
        (demo / "state").mkdir()
        # Product password hashing; no existing password store is opened.
        from backend.infrastructure.security.lan_passwords import PasswordStore, hash_password
        PasswordStore(demo / "htpasswd").write({"demo-admin": hash_password(secrets.token_urlsafe(24))})
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        base_url = f"http://localhost:{port}"
        with (demo / "runtime.log").open("w") as log:
            try:
                children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app.main:app",
                    "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=log))
                for _ in range(100):
                    if children[0].poll() is not None:
                        raise RuntimeError("Demo API exited during startup")
                    try:
                        with httpx.Client(trust_env=False) as client:
                            if client.get(base_url + "/healthz").status_code == 200:
                                break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.2)
                else:
                    raise TimeoutError("Demo API startup timed out")
                capture = [sys.executable, str(ROOT / "scripts/capture-readme-screenshots.py"),
                           "--base-url", base_url, "--runtime-dir", str(demo)]
                subprocess.run([*capture, "--phase", "empty"], cwd=ROOT, env=env, check=True)
                children.append(subprocess.Popen([sys.executable, "-m", "backend.workers.task_worker",
                    "--poll-seconds", "0.2", "--max-workers", "2",
                    "--task-type", "fetch", "--task-type", "card_build",
                    "--task-type", "content_enrich", "--task-type", "media_download",
                    "--task-type", "card_finalize"],
                    cwd=ROOT, env=env, stdout=log, stderr=log))
                spec = importlib.util.spec_from_file_location("readme_seed", ROOT / "scripts/seed-readme-demo.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                manifest = module.seed(base_url)
                (demo / "manifest.json").write_text(json.dumps(manifest))
                (demo / "manifest.json").chmod(0o600)
                subprocess.run([*capture, "--phase", "seeded"], cwd=ROOT, env=env, check=True)
                # Headless Chromium cannot click the optional host-permission
                # prompt. Pre-grant only the disposable API host in a temporary
                # copy of the built extension; keep its UI and runtime code intact.
                extension = demo / "extension"
                shutil.copytree(ROOT / "clients/save-to-infoos/dist", extension)
                extension_manifest = json.loads((extension / "manifest.json").read_text())
                extension_manifest["host_permissions"] = ["http://localhost/*"]
                (extension / "manifest.json").write_text(json.dumps(extension_manifest))
                subprocess.run([sys.executable, str(ROOT / "scripts/capture-readme-extension.py"),
                                "--base-url", base_url, "--runtime-dir", str(demo)],
                               cwd=ROOT, env=env, check=True)
            except Exception:
                # The log is temporary and can contain local paths; do not publish it.
                print("Demo failed; temporary runtime log tail:", file=sys.stderr)
                print("\n".join((demo / "runtime.log").read_text().splitlines()[-25:]), file=sys.stderr)
                raise
            finally:
                for child in reversed(children):
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait()
        print("Demo stopped; temporary database, account, logs and browser profiles removed.")


if __name__ == "__main__":
    main()
