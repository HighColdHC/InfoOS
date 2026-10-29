from __future__ import annotations

from backend.infrastructure.browser import runtime_policy
from backend.application.usecases.browser_session import prepare_opencli_browser_session
from backend.infrastructure.connectors.opencli import OpenCLIConnector


def test_deployed_native_runtime_is_blocked(monkeypatch):
    monkeypatch.setenv("INFOOS_STATE_HOME", "/var/lib/infoos/state")
    monkeypatch.setenv("INFOOS_CHROME_PROFILE_DIR", "/var/lib/infoos/state/chrome-profile")
    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "native")

    assert "native/systemd runtime is read-only" in (
        runtime_policy.browser_mutation_block_reason() or ""
    )


def test_host_cannot_claim_supervised_container_runtime(monkeypatch, tmp_path):
    missing_marker = tmp_path / "missing-dockerenv"
    monkeypatch.setattr(runtime_policy, "_DOCKER_MARKER", missing_marker)
    monkeypatch.setenv("INFOOS_STATE_HOME", "/var/lib/infoos/state")
    monkeypatch.setenv("INFOOS_CHROME_PROFILE_DIR", "/var/lib/infoos/state/chrome-profile")
    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "supervised-container")

    assert "container ownership was not proven" in (
        runtime_policy.browser_mutation_block_reason() or ""
    )


def test_supervised_container_with_fixed_profile_is_allowed(monkeypatch, tmp_path):
    marker = tmp_path / ".dockerenv"
    marker.touch()
    monkeypatch.setattr(runtime_policy, "_DOCKER_MARKER", marker)
    monkeypatch.setenv("INFOOS_STATE_HOME", "/var/lib/infoos/state")
    monkeypatch.setenv("INFOOS_CHROME_PROFILE_DIR", "/var/lib/infoos/state/chrome-profile")
    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "supervised-container")

    assert runtime_policy.browser_mutation_block_reason() is None


def test_native_development_profile_remains_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("INFOOS_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("INFOOS_CHROME_PROFILE_DIR", str(tmp_path / "state/chrome-profile"))
    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "native")

    assert runtime_policy.browser_mutation_block_reason() is None


def test_all_browser_entrypoints_refuse_deployed_native_runtime(monkeypatch):
    monkeypatch.setenv("INFOOS_STATE_HOME", "/var/lib/infoos/state")
    monkeypatch.setenv("INFOOS_CHROME_PROFILE_DIR", "/var/lib/infoos/state/chrome-profile")
    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "native")

    application_result = prepare_opencli_browser_session()
    connector = OpenCLIConnector(
        connector_id="x-test",
        command=["twitter", "profile"],
        source_platform="x",
    )

    assert application_result["status"] == "blocked"
    assert application_result["browser_launch_method"] == "blocked_runtime"
    assert "Container Browser Runtime is required" in application_result["issues"][0]
    try:
        connector.fetch_new_items(None)
    except RuntimeError as error:
        assert "Container Browser Runtime is required" in str(error)
    else:
        raise AssertionError("browser-backed connector must fail closed")
