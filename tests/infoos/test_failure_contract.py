import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.workflows.run_once import run_once
from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
from backend.infrastructure.storage.repositories import SourceRepository
from tests.infoos._factories import make_supported_source_payload, opencli_command_startswith


def test_run_once_reports_partial_failure_when_one_connector_fails(tmp_path, monkeypatch):
    """Two sources: one with an opencli source_type (not supported by the
    SQLite projection -> will fail validation), one that's missing connector_id.
    The new database_source_to_connector_config rejects unsupported platform/source_type
    combinations, so a source with platform/source_type that isn't in
    SUPPORTED_EXECUTION is disabled rather than failed at runtime. To get a
    real partial_failure we seed two valid X list sources and have one of them
    raise in connector.fetch_new_items."""
    ensure_workbench_seeded(tmp_path)
    # First source: a valid x/list source whose connector will raise.
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="x_will_fail",
        platform="x",
        source_type="list",
        identifier="111",
        connector_id="opencli_x_will_fail",
    ))
    # Second source: a valid x/list source that will succeed (we'll mock opencli).
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="x_will_succeed",
        platform="x",
        source_type="list",
        identifier="222",
        connector_id="opencli_x_will_succeed",
    ))

    def fake_opencli_run(command, **kwargs):
        from types import SimpleNamespace
        # If the failing source's connector runs, simulate a missing binary.
        if opencli_command_startswith(command, "opencli", "twitter", "list-tweets"):
            identifier = command[command.index("list-tweets") + 1]
            if identifier == "111":
                raise FileNotFoundError("opencli")
            if identifier == "222":
                payload = [
                    {
                        "id": "x-1",
                        "url": "https://x.com/a/1",
                        "text": "Useful local-first intelligence angle",
                        "created_at": "2026-04-24T09:00:00Z",
                        "author": {"name": "a", "username": "a"},
                    }
                ]
                return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
        return SimpleNamespace(stdout="[]", stderr="", returncode=0)

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli_run)

    result = run_once(tmp_path)

    assert result["status"] == "partial_failure"
    assert result["sources"][0]["connector_id"] == "opencli_x_will_fail"
    assert result["sources"][0]["status"] == "connector_failed"
    assert result["sources"][1]["connector_id"] == "opencli_x_will_succeed"
    assert result["sources"][1]["status"] == "success"
    assert result["cards"]["generated"] == 1
    assert result["errors"][0]["stage"] == "connector"
    assert (tmp_path / "data" / "diagnostics" / "errors.jsonl").exists()


def test_run_once_fails_when_all_connectors_fail(tmp_path, monkeypatch):
    """One valid x/list source whose connector raises. Result: failed."""
    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="x_will_fail",
        platform="x",
        source_type="list",
        identifier="111",
        connector_id="opencli_x_will_fail",
    ))

    def fail_run(command, **kwargs):
        if opencli_command_startswith(command, "opencli", "twitter", "list-tweets"):
            raise FileNotFoundError(command[0])
        raise FileNotFoundError(command[0])

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fail_run)

    result = run_once(tmp_path)

    assert result["status"] == "failed"
    assert result["cards"]["generated"] == 0
    assert result["errors"][0]["severity"] == "error"
