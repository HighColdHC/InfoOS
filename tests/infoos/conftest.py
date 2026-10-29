from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.infrastructure.opencli.commands import OpenCLIResult
from backend.infrastructure.opencli import tab_lease
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


_TEST_CLIENT_INIT = TestClient.__init__


@pytest.fixture(autouse=True)
def isolate_default_homes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep default paths away from the developer's real data."""
    monkeypatch.setenv("INFOOS_DATA_HOME", str(tmp_path / "default-data-home"))
    monkeypatch.setenv("INFOOS_STATE_HOME", str(tmp_path / "default-state-home"))


@pytest.fixture(autouse=True)
def same_origin_test_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give existing browser-style API tests a same-origin client default."""

    def _same_origin_init(self, *args, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("origin", "http://testserver")
        headers.setdefault("sec-fetch-site", "same-origin")
        _TEST_CLIENT_INIT(self, *args, headers=headers, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", _same_origin_init)


@pytest.fixture(autouse=True)
def bootstrap_runtime_storage(request: pytest.FixtureRequest, tmp_path: Path) -> None:
    """Explicitly provide the runtime schema expected by ordinary tests."""
    if request.node.get_closest_marker("no_storage_bootstrap") is None:
        initialize_runtime_storage(tmp_path)


@pytest.fixture(autouse=True)
def isolate_opencli_tab_leases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Never let unit tests close or lock the user's real OpenCLI sessions."""
    monkeypatch.setattr(tab_lease, "_lease_lock_path", lambda key: tmp_path / "opencli-tab-leases" / f"{hash(key)}.lock")
    monkeypatch.setattr(tab_lease, "close_site_session", lambda *args, **kwargs: OpenCLIResult())
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **_kwargs: {"status": "ready", "browser_headless": False},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: None,
    )


@pytest.fixture
def isolate_opencli_lock(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_PATH", str(tmp_path / "opencli-browser-session.lock"))
