"""Signal only same-UID webauth processes; the base supervisor restarts them."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import time

import httpx


def webauth_pids() -> set[int]:
    found = set()
    for path in Path("/proc").glob("[0-9]*/comm"):
        try:
            if path.stat().st_uid == os.getuid() and path.read_text().strip() == "webauth":
                found.add(int(path.parent.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return found


def webauth_ready() -> bool:
    transport = httpx.HTTPTransport(uds=os.environ.get("INFOOS_WEBAUTH_SOCKET", "/tmp/webauth.sock"))
    try:
        with httpx.Client(transport=transport, timeout=0.5) as client:
            return client.get("http://localhost/auth").status_code == 401
    except (httpx.HTTPError, OSError):
        return False


def reload_and_invalidate_sessions() -> None:
    pids = webauth_pids()
    if not pids:
        raise RuntimeError("webauth unavailable")
    for pid in pids:
        os.kill(pid, signal.SIGHUP)
    # Reload alone does not clear tokens or regenerate secure-cookie keys.
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = webauth_pids()
        if current and not current.intersection(pids) and webauth_ready():
            return
        time.sleep(0.1)
    raise RuntimeError("webauth restart not confirmed")
