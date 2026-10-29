from __future__ import annotations

from backend.application.setup.environment_snapshot import refresh_component_snapshot
from backend.infrastructure.runtime.setup_state import COMPONENT_IDS, load_state


def test_refresh_component_snapshot_persists_all_eight_with_one_timestamp(tmp_path):
    state_path = tmp_path / "setup-state.json"
    calls: list[str] = []

    def probe(component_id: str):
        calls.append(component_id)
        return {"status": "installed", "version": f"v-{component_id}"}

    payload = refresh_component_snapshot(
        state_path,
        probes={component_id: (lambda value=component_id: probe(value)) for component_id in COMPONENT_IDS},
        now=lambda: "2026-07-16T09:00:00Z",
    )

    assert calls == list(COMPONENT_IDS)
    assert payload["snapshot_at"] == "2026-07-16T09:00:00Z"
    state = load_state(state_path)
    assert state.component_snapshot_at == "2026-07-16T09:00:00Z"
    assert all(item.last_checked == state.component_snapshot_at for item in state.components.values())
    assert state.components["ffmpeg"].version == "v-ffmpeg"


def test_refresh_component_snapshot_isolates_probe_failure(tmp_path):
    state_path = tmp_path / "setup-state.json"

    def failed_probe():
        raise RuntimeError("machine-specific secret path")

    probes = {
        component_id: (failed_probe if component_id == "local_asr" else lambda: {"status": "installed", "version": "1"})
        for component_id in COMPONENT_IDS
    }

    refresh_component_snapshot(
        state_path,
        probes=probes,
        now=lambda: "2026-07-16T09:00:00Z",
    )
    state = load_state(state_path)

    assert state.components["local_asr"].status == "unknown"
    assert state.components["local_asr"].version is None
    assert state.components["local_asr"].detail_code == "local_asr_probe_failed"
    assert state.components["sqlite"].status == "installed"
