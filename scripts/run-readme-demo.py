#!/usr/bin/env python3
"""Capture an isolated database copy, then delete the entire private workspace."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from readme_redaction import scrub_copy
from readme_runtime import install_runtime_facts
from readme_screenshot_audit import audit_images

ROOT = Path(__file__).resolve().parents[1]


def serve(port: int):
    """Install outbound/process denial before importing the product app."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def local(address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise PermissionError("Screenshot instance denies outbound connections")

    def guarded_connect(self, address):
        local(address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        local(address)
        return connect_ex(self, address)

    def guarded_resolve(host, *args, **kwargs):
        if host not in {None, "127.0.0.1", "::1", "localhost"}:
            raise PermissionError("Screenshot instance denies outbound DNS")
        return getaddrinfo(host, *args, **kwargs)

    def audit(event, args):
        if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec"}:
            raise PermissionError("Screenshot instance denies subprocesses")

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_resolve
    sys.addaudithook(audit)
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=port, loop="asyncio", access_log=False)


def capture_instance(directory: Path, phase: str):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("INFOOS_", "LARK_", "FEISHU_"))}
    home = directory / phase
    home.mkdir(mode=0o700, exist_ok=True)
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
                "INFOOS_STATE_HOME": str(home / "state"), "INFOOS_DATA_HOME": str(home / "data"),
                "INFOOS_HOME": str(ROOT), "INFOOS_WEBAUTH_PASSWORD_FILE": str(home / "htpasswd"),
                "INFOOS_CONTAINER_HEALTH_PATH": str(home / "container-health.json"),
                "INFOOS_CHROME_PROFILE_DIR": str(home / "unused-chrome-profile"),
                "INFOOS_CONFIG_PATH": str(home / "config.yaml"),
                "INFOOS_CONFIG_DIR": str(home / "config"), "PYTHONPATH": str(ROOT)})
    (home / "data").mkdir(exist_ok=True)
    if phase == "live":
        (home / "state").mkdir(exist_ok=True)
        install_runtime_facts(home, json.loads((directory / "runtime-facts.json").read_text()))
    if phase == "live" and (directory / "snapshot.sqlite3").exists():
        shutil.move(directory / "snapshot.sqlite3", home / "data/infoos.sqlite3")
    from backend.infrastructure.security.lan_passwords import PasswordStore, hash_password
    PasswordStore(home / "htpasswd").write({"demo-admin": hash_password(secrets.token_urlsafe(32))})
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    with (home / "runtime.log").open("w") as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", str(port)],
                                 cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            with httpx.Client(trust_env=False) as client:
                for _ in range(300):
                    if child.poll() is not None:
                        raise RuntimeError("Isolated API could not open the database; stopped without changing production")
                    try:
                        if client.get(base_url + "/healthz").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.2)
                else:
                    raise TimeoutError("Isolated API startup timed out")
            subprocess.run([sys.executable, str(ROOT / "scripts/capture-readme-screenshots.py"),
                            "--base-url", base_url, "--runtime-dir", str(home), "--phase", phase],
                           cwd=ROOT, check=True)
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            print(f"Isolated API stopped ({phase}); pid={child.pid}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
        return
    if not args.snapshot:
        parser.error("--snapshot is required")
    snapshot = args.snapshot.resolve()
    directory = snapshot.parent
    if directory.parent != Path("/tmp").resolve() or not directory.name.startswith("infoos-shots-"):
        parser.error("Snapshot must be inside a disposable /tmp/infoos-shots-* directory; it will be deleted")
    if snapshot.name != "snapshot.sqlite3":
        parser.error("Snapshot must be named snapshot.sqlite3")
    try:
        if not (ROOT / "frontend/dist/index.html").exists():
            raise RuntimeError("Build frontend first")
        removed = scrub_copy(snapshot)
        print("Local credential tables cleared: " + ", ".join(removed), flush=True)
        capture_instance(directory, "empty")
        capture_instance(directory, "live")
        summary = {"captures": [json.loads((directory / phase / "capture-report.json").read_text())
                                for phase in ("empty", "live")],
                   "pixel_audit": audit_images(directory / "live/data/infoos.sqlite3", directory, ROOT / "docs/images/features")}
        report_dir = ROOT / ".codex-tmp"
        report_dir.mkdir(exist_ok=True)
        (report_dir / "readme-capture-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        assert summary["pixel_audit"]["ocr_hits"] == 0, "OCR matched original private identifiers"
        assert not any(summary["pixel_audit"]["text_hits"].values()), "Source text matched original private identifiers"
        print("OCR and pixel audit passed for all images", flush=True)
    finally:
        shutil.rmtree(directory)
        print(f"Removed private workspace: {directory.name}; exists={directory.exists()}", flush=True)


if __name__ == "__main__":
    main()
