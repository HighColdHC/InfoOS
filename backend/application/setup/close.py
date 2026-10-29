"""Mark setup complete.

Writes ``current_step=complete`` plus ``first_fetch`` to the setup
state file, and emits a sentinel flag file at
``~/.infoos/setup-complete.flag``. Both writes are best-effort: a
failure here does not crash the wizard; the next session will just
re-run Card 4.
"""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

from pathlib import Path

from backend.application.setup.state import load_state, save_state


def mark_setup_complete(
    state_path: Path,
    flag_path: Path,
    *,
    fetched_count: int,
) -> None:
    state = load_state(state_path)
    state.current_step = "complete"
    if "first_fetch" not in state.completed_steps:
        state.completed_steps.append("first_fetch")
    state.first_fetch.ok = True
    state.first_fetch.fetched_count = fetched_count
    state.first_fetch.ran_at = utc_now_micro_z()
    save_state(state_path, state)

    from backend.application.setup.environment_snapshot import refresh_component_snapshot

    refresh_component_snapshot(state_path)

    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(
        f"setup complete at {state.first_fetch.ran_at}\n",
        encoding="utf-8",
    )
