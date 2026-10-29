"""Setup close: mark state as complete and emit a flag file."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.setup import close  # noqa: E402
from backend.application.setup.state import load_state  # noqa: E402


@pytest.fixture(autouse=True)
def skip_real_component_probe(monkeypatch):
    monkeypatch.setattr(
        "backend.application.setup.environment_snapshot.refresh_component_snapshot",
        lambda _state_path: {},
    )


def test_mark_setup_complete_writes_state(tmp_path):
    state_path = tmp_path / "setup-state.json"
    flag_path = tmp_path / "setup-complete.flag"
    close.mark_setup_complete(state_path, flag_path, fetched_count=5)
    loaded = load_state(state_path)
    assert loaded.current_step == "complete"
    assert "first_fetch" in loaded.completed_steps
    assert loaded.first_fetch.ok is True
    assert loaded.first_fetch.fetched_count == 5
    assert flag_path.is_file()


def test_mark_setup_complete_idempotent(tmp_path):
    state_path = tmp_path / "setup-state.json"
    flag_path = tmp_path / "setup-complete.flag"
    close.mark_setup_complete(state_path, flag_path, fetched_count=3)
    close.mark_setup_complete(state_path, flag_path, fetched_count=7)
    loaded = load_state(state_path)
    assert loaded.first_fetch.fetched_count == 7  # last write wins


def test_mark_setup_complete_creates_parent_dir(tmp_path):
    state_path = tmp_path / "nested" / "state.json"
    flag_path = tmp_path / "nested" / "complete.flag"
    close.mark_setup_complete(state_path, flag_path, fetched_count=0)
    assert state_path.is_file()
    assert flag_path.is_file()


def test_mark_setup_complete_refreshes_component_snapshot(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "backend.application.setup.environment_snapshot.refresh_component_snapshot",
        lambda state_path: calls.append(state_path) or {},
    )
    state_path = tmp_path / "setup-state.json"

    close.mark_setup_complete(state_path, tmp_path / "complete.flag", fetched_count=1)

    assert calls == [state_path]
