"""Setup state: load/save ~/.infoos/setup-state.json with atomic write."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.setup.state import (  # noqa: E402
    AccountState,
    AccountStatus,
    COMPONENT_IDS,
    DEFAULT_STATE,
    SetupState,
    load_state,
    save_state,
)


def test_default_state_has_all_required_keys():
    state = SetupState.from_dict({})
    assert state.version == 1
    assert state.current_step == "fresh"
    assert state.completed_steps == []
    assert state.chrome.binary_path is None
    assert state.extension.loaded is False
    assert state.accounts["x"].status == AccountStatus.UNKNOWN
    assert state.first_fetch.ok is None
    assert state.wizard_opens == 0
    assert tuple(state.components) == COMPONENT_IDS
    assert all(component.status == "unknown" for component in state.components.values())
    assert all(component.version is None for component in state.components.values())
    assert state.component_snapshot_at is None


def test_load_state_returns_default_when_missing(tmp_path):
    state = load_state(tmp_path / "missing.json")
    assert state.current_step == "fresh"


def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = SetupState.from_dict({})
    state.current_step = "install_extension"
    state.completed_steps = ["launch_chrome"]
    state.chrome.binary_path = "/Applications/Google Chrome.app/.../Google Chrome"
    state.chrome.running = True
    state.accounts["x"] = AccountState(status=AccountStatus.VERIFIED)
    save_state(path, state)

    loaded = load_state(path)
    assert loaded.current_step == "install_extension"
    assert loaded.completed_steps == ["launch_chrome"]
    assert loaded.chrome.running is True
    assert loaded.accounts["x"].status == AccountStatus.VERIFIED


@pytest.mark.no_storage_bootstrap
def test_save_state_writes_atomically(tmp_path, monkeypatch):
    """Writing to a fresh file must use write-then-rename, not direct write."""
    path = tmp_path / "state.json"
    save_state(path, SetupState.from_dict({}))
    # No .tmp or .partial files should be left behind
    leftover = [p for p in tmp_path.iterdir() if p.name != "state.json"]
    assert leftover == []


def test_load_state_recovers_from_corrupt_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not valid json", encoding="utf-8")
    state = load_state(path)
    # Corrupt file → return default; user can re-run wizard
    assert state.current_step == "fresh"


def test_from_dict_preserves_unknown_keys_for_forward_compat():
    raw = {"version": 1, "current_step": "complete", "future_field": {"x": 1}}
    state = SetupState.from_dict(raw)
    assert state.raw == raw


def test_legacy_state_migrates_eight_components_without_active_probe():
    state = SetupState.from_dict({"version": 1, "chrome": {"version": "legacy-ignored"}})

    assert tuple(state.components) == COMPONENT_IDS
    assert state.components["dedicated_chrome"].status == "unknown"
    assert state.components["dedicated_chrome"].version is None


def test_component_snapshot_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = SetupState.from_dict({})
    state.components["sqlite"].status = "installed"
    state.components["sqlite"].version = "3.46.0"
    state.components["sqlite"].last_checked = "2026-07-16T08:00:00Z"
    state.components["sqlite"].detail_code = None
    state.component_snapshot_at = "2026-07-16T08:00:00Z"

    save_state(path, state)
    loaded = load_state(path)

    assert loaded.components["sqlite"].status == "installed"
    assert loaded.components["sqlite"].version == "3.46.0"
    assert loaded.component_snapshot_at == "2026-07-16T08:00:00Z"


def test_account_persistence_defaults_false_for_old_state_and_round_trips_unknown_fields(tmp_path):
    path = tmp_path / "state.json"
    state = SetupState.from_dict({
        "accounts": {
            "x": {
                "status": "verified",
                "last_checked": "old",
                "future_account_field": {"kept": True},
            }
        }
    })

    assert state.accounts["x"].persisted is False
    state.accounts["x"].persisted = True
    state.accounts["x"].persistence_checked_at = "2026-07-22T00:00:00Z"
    state.accounts["x"].persistence_stage = "verified"
    save_state(path, state)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["accounts"]["x"]["future_account_field"] == {"kept": True}
    assert raw["accounts"]["x"]["persisted"] is True


def test_new_account_state_serializes_persistence_fields(tmp_path):
    path = tmp_path / "state.json"
    state = SetupState.from_dict({})
    state.accounts["reddit"] = AccountState(
        status=AccountStatus.FAILED,
        persisted=False,
        persistence_checked_at="2026-07-22T00:00:00Z",
        persistence_stage="restart",
        persistence_error="Dedicated Chrome did not restart gracefully",
    )

    save_state(path, state)
    loaded = load_state(path).accounts["reddit"]

    assert loaded.persisted is False
    assert loaded.persistence_stage == "restart"
    assert loaded.persistence_error == "Dedicated Chrome did not restart gracefully"
