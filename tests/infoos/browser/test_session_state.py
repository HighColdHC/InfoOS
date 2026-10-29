import json
from pathlib import Path

import pytest

from backend.infrastructure.browser.session_state import (
    SUPERVISED_CONTAINER_BINDING_PROOF,
    BrowserSessionState,
    CACHE_TTL_SECONDS,
    is_alive,
    is_proven_dedicated_binding,
    read,
    write,
    _cache_clear,
)


@pytest.fixture
def tmp_json_path(tmp_path, monkeypatch):
    """Override the default JSON path to a tmp file."""
    json_path = tmp_path / "browser-session.json"
    monkeypatch.setattr("backend.infrastructure.browser.session_state.JSON_PATH", json_path)
    _cache_clear()
    return json_path


def _sample_state() -> BrowserSessionState:
    return BrowserSessionState(
        schema_version=1,
        profile_dir="/Users/test/.infoos/chrome-profile",
        context_id="abc123",
        alias="infoos-fetch",
        started_at="2026-06-12T09:30:00Z",
        last_health_at="2026-06-12T10:15:00Z",
        last_health_status="ready",
    )


def test_read_returns_none_when_file_missing(tmp_json_path):
    """read() returns None when the JSON file does not exist."""
    assert read() is None


def test_read_returns_state_when_file_valid(tmp_json_path):
    """read() returns a BrowserSessionState when JSON is well-formed."""
    tmp_json_path.write_text(json.dumps({
        "schema_version": 1,
        "profile_dir": "/Users/test/.infoos/chrome-profile",
        "context_id": "abc123",
        "alias": "infoos-fetch",
        "started_at": "2026-06-12T09:30:00Z",
        "last_health_at": "2026-06-12T10:15:00Z",
        "last_health_status": "ready",
    }))
    _cache_clear()
    state = read()
    assert state is not None
    assert state.context_id == "abc123"
    assert state.alias == "infoos-fetch"


def test_read_returns_none_when_schema_version_mismatch(tmp_json_path):
    """read() returns None when schema_version != 1."""
    tmp_json_path.write_text(json.dumps({
        "schema_version": 99,
        "context_id": "abc",
        "alias": "infoos-fetch",
        "profile_dir": "/x",
        "started_at": "t", "last_health_at": "t", "last_health_status": "ready",
    }))
    _cache_clear()
    assert read() is None


def test_write_creates_file_and_read_returns_state(tmp_json_path):
    """write() persists state, read() returns it back."""
    state = _sample_state()
    write(state)
    _cache_clear()
    assert read() == state


def test_is_alive_returns_false_for_none():
    assert is_alive(None) is False


def test_is_alive_returns_false_for_wrong_alias():
    state = _sample_state()
    state = BrowserSessionState(**{**state.__dict__, "alias": "wrong-alias"})
    assert is_alive(state) is False


def test_is_alive_returns_true_for_valid_state():
    assert is_alive(_sample_state()) is True


def test_schema_v2_dedicated_binding_round_trips_and_is_alive(tmp_json_path):
    state = BrowserSessionState(
        schema_version=2,
        profile_dir="/Users/test/.infoos/chrome-profile",
        context_id="ctx-dedicated",
        alias="infoos-fetch",
        started_at="2026-07-17T00:00:00Z",
        last_health_at="2026-07-17T00:00:00Z",
        last_health_status="ready",
        chrome_pid=1234,
        binding_proof="new_context_after_dedicated_launch",
    )

    write(state)
    _cache_clear()
    loaded = read()

    assert loaded == state
    assert is_alive(loaded) is True
    assert is_proven_dedicated_binding(
        loaded,
        profile_dir=Path("/Users/test/.infoos/chrome-profile"),
    ) is True


def test_schema_v1_is_not_a_proven_dedicated_binding():
    assert is_proven_dedicated_binding(
        _sample_state(),
        profile_dir=Path("/Users/test/.infoos/chrome-profile"),
    ) is False


def test_supervised_container_proof_is_allowed_but_unknown_proof_is_rejected():
    state = BrowserSessionState(
        schema_version=2,
        profile_dir="/var/lib/infoos/state/chrome-profile",
        context_id="ctx-container",
        alias="infoos-fetch",
        started_at="2026-07-22T00:00:00Z",
        last_health_at="2026-07-22T00:00:00Z",
        last_health_status="ready",
        chrome_pid=1234,
        binding_proof=SUPERVISED_CONTAINER_BINDING_PROOF,
    )

    assert is_proven_dedicated_binding(
        state,
        profile_dir=Path("/var/lib/infoos/state/chrome-profile"),
    ) is True
    assert is_proven_dedicated_binding(
        BrowserSessionState(**{**state.__dict__, "binding_proof": "bridge_connected"}),
        profile_dir=Path("/var/lib/infoos/state/chrome-profile"),
    ) is False


def test_read_caches_for_5_seconds(tmp_json_path, monkeypatch):
    """read() caches result for 5s, then refreshes from disk."""
    now = 1000.0
    monkeypatch.setattr("backend.infrastructure.browser.session_state.time.monotonic", lambda: now)
    tmp_json_path.write_text(json.dumps({
        "schema_version": 1, "context_id": "abc", "alias": "infoos-fetch",
        "profile_dir": "/x", "started_at": "t", "last_health_at": "t",
        "last_health_status": "ready",
    }))
    _cache_clear()
    first = read()
    # Mutate the file; second read should return cached first
    tmp_json_path.write_text(json.dumps({
        "schema_version": 1, "context_id": "DIFFERENT", "alias": "infoos-fetch",
        "profile_dir": "/x", "started_at": "t", "last_health_at": "t",
        "last_health_status": "ready",
    }))
    second = read()
    assert second.context_id == "abc"  # cached
    # After cache expires, read returns fresh data
    now += CACHE_TTL_SECONDS + 0.1
    third = read()
    assert third.context_id == "DIFFERENT"
