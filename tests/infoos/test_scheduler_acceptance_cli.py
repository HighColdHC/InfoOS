from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from backend.domain.sources.registry import configurable_platforms
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import SourceRepository


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run-infoos-scheduler-acceptance.py"


def _module():
    spec = importlib.util.spec_from_file_location("scheduler_acceptance", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(base_dir: Path, platform: str, index: int) -> None:
    definition = next(
        item for item in configurable_platforms() if item.platform == platform
    )
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": f"{platform}-{index}",
            "platform": platform,
            "source_type": definition.source_types[0].source_type,
            "identifier": f"{platform}-{index}",
            "display_name": f"{platform}-{index}",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
        }
    )


def test_scheduler_acceptance_requires_exact_registry_platform_set(tmp_path: Path) -> None:
    module = _module()
    for definition in configurable_platforms()[:-1]:
        for index in range(5):
            _source(tmp_path, definition.platform, index)

    with pytest.raises(ValueError, match="below source minimum"):
        module.canonical_source_set(tmp_path)


def test_scheduler_acceptance_selects_five_distinct_sources_per_platform(
    tmp_path: Path,
) -> None:
    module = _module()
    for definition in configurable_platforms():
        for index in range(6):
            _source(tmp_path, definition.platform, index)

    selected = module.canonical_source_set(tmp_path)

    assert set(selected) == {item.platform for item in configurable_platforms()}
    assert all(len(source_ids) == 5 for source_ids in selected.values())
    assert all(len(set(source_ids)) == 5 for source_ids in selected.values())


def _insert_chain_task(
    base_dir: Path,
    *,
    task_id: str,
    source_id: str,
    chain_id: str,
    task_type: str = "fetch",
    status: str = "success",
    created_at: str = "2026-07-23T10:00:01Z",
    claimed: bool = True,
    artifacts: dict[str, object] | None = None,
) -> None:
    db_path = resolve_sqlite_path(base_dir)
    summary = {"chain_id": chain_id, **(artifacts or {})}
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            insert into tasks(
              task_id, task_type, target_kind, target_id, status, depends_on,
              artifacts_summary, started_at, finished_at, created_at, updated_at
            ) values (?, ?, 'source', ?, ?, '[]', ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                task_type,
                source_id,
                status,
                json.dumps(summary),
                created_at if claimed else None,
                created_at,
                created_at,
                created_at,
            ),
        )
        if claimed:
            conn.execute(
                """
                insert into task_events(
                  event_id, task_id, chain_id, event_type, status, payload, occurred_at
                ) values (?, ?, ?, 'claimed', 'running', '{}', ?)
                """,
                (f"event-{task_id}", task_id, chain_id, created_at),
            )


def test_chain_evidence_uses_sqlite_rows_not_artifact_claims(tmp_path: Path) -> None:
    module = _module()
    _source(tmp_path, configurable_platforms()[0].platform, 0)
    source_id = f"{configurable_platforms()[0].platform}-0"
    _insert_chain_task(
        tmp_path,
        task_id="fetch-1",
        source_id=source_id,
        chain_id="chain-1",
        artifacts={"raw_item_ids": ["ghost-raw"], "card_id": "ghost-card"},
    )

    evidence = module._chain_evidence(
        tmp_path,
        "chain-1",
        source_id=source_id,
        scheduler_started_at="2026-07-23T10:00:00Z",
    )

    assert evidence["raw_item_artifact_count"] == 1
    assert evidence["raw_item_count"] == 0
    assert evidence["card_artifact_count"] == 1
    assert evidence["card_count"] == 0
    assert "raw_item_missing" in module._completion_failures(
        {"fetch_status": "success", "evidence": evidence}
    )
    assert "active_card_missing" in module._completion_failures(
        {"fetch_status": "success", "evidence": evidence}
    )


def test_chain_evidence_rejects_failed_historical_or_unclaimed_tasks(
    tmp_path: Path,
) -> None:
    module = _module()
    platform = configurable_platforms()[0].platform
    _source(tmp_path, platform, 0)
    source_id = f"{platform}-0"
    _insert_chain_task(
        tmp_path,
        task_id="fetch-old",
        source_id=source_id,
        chain_id="chain-old",
        status="failed",
        created_at="2026-07-23T09:59:59Z",
        claimed=False,
    )

    evidence = module._chain_evidence(
        tmp_path,
        "chain-old",
        source_id=source_id,
        scheduler_started_at="2026-07-23T10:00:00Z",
    )
    failures = module._completion_failures(
        {"fetch_status": "failed", "evidence": evidence}
    )

    assert "fetch_not_success" in failures
    assert "chain_failed_or_blocked" in failures
    assert "historical_chain_task" in failures
    assert "worker_claim_missing" in failures


@pytest.mark.parametrize("error_type", [RuntimeError, sqlite3.OperationalError])
def test_scheduler_acceptance_redacts_exception_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error_type: type[Exception],
) -> None:
    module = _module()
    secret = "sk-abcdefghijklmnop1234"

    def fail(*_args, **_kwargs):
        raise error_type(f"provider rejected {secret}")

    monkeypatch.setattr(module, "run_acceptance", fail)

    exit_code = module.main(
        [
            "--base-dir",
            str(tmp_path),
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    assert exit_code == 2
    assert secret not in capsys.readouterr().err


def test_scheduler_plan_rejects_historical_task_reuse() -> None:
    module = _module()
    planned = {
        "task-1": {
            "task_id": "task-1",
            "source_id": "source-1",
            "artifacts": {},
        }
    }

    with pytest.raises(RuntimeError, match="reused=1"):
        module._validate_scheduler_plan(
            ["task-1"],
            planned,
            selected_ids={"source-1"},
            existing_task_ids={"task-1"},
        )


def test_scheduler_chain_validation_rejects_duplicate_chain_ids() -> None:
    module = _module()
    terminal = {
        "task-1": {
            "source_id": "source-1",
            "started_at": "2026-07-23T10:00:01Z",
            "claimed_event_count": 1,
            "artifacts": {"chain_id": "same-chain"},
        },
        "task-2": {
            "source_id": "source-2",
            "started_at": "2026-07-23T10:00:02Z",
            "claimed_event_count": 1,
            "artifacts": {"chain_id": "same-chain"},
        },
    }

    with pytest.raises(RuntimeError, match="unique chain_id"):
        module._validated_chains(terminal, expected_count=2)


def test_scheduler_acceptance_main_preserves_dot_runtime_base_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    observed: list[Path] = []

    def passed(base_dir: Path, **_kwargs):
        observed.append(base_dir)
        return {"status": "passed"}

    monkeypatch.setattr(module, "run_acceptance", passed)

    exit_code = module.main(
        [
            "--base-dir",
            ".",
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    assert exit_code == 0
    assert observed == [Path(".")]
