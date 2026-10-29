"""The benchmark opens named copies and reports reproducible SQL plans."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sqlite3
from pathlib import Path

from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database
from tests.infoos.storage.migration_test_helpers import drop_post_v3_schema

SCRIPT = Path(__file__).parents[2] / "scripts/benchmark-task-read-paths.py"


def test_benchmark_requires_copy_path_and_reports_plans(tmp_path: Path) -> None:
    missing = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert missing.returncode != 0
    db = tmp_path / "backup.sqlite3"
    migrate_database(db)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--repeats", "1"],
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["probes"]["task_recent"]["explain"]
    assert payload["probes"]["card_task_ids"]["rows"] == 0


def test_benchmark_rejects_configured_data_directory_without_copy_ack(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = data_dir / "infoos.sqlite3"
    migrate_database(db)
    env = {**os.environ, "INFOOS_DATA_HOME": str(data_dir)}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--repeats", "1"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Refusing a default data directory" in result.stderr


def test_benchmark_migrates_only_with_explicit_flag(tmp_path: Path) -> None:
    db = tmp_path / "v2-copy.sqlite3"
    migrate_database(db)
    with sqlite3.connect(db) as conn:
        for name in (
            "idx_tasks_created_at_task_id", "idx_tasks_fetch_schedule",
            "idx_run_history_scheduled_run", "idx_acquisition_checkpoints_run_task",
            "idx_tasks_fetch_target",
        ):
            conn.execute(f'drop index "{name}"')
        drop_post_v3_schema(conn)
        conn.execute("pragma user_version=2")
    baseline = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--repeats", "1"],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(baseline.stdout)["user_version"] == 2
    upgraded = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--migrate-copy", "--repeats", "1"],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(upgraded.stdout)["migration"] == {
        "before_version": 2, "after_version": LATEST_VERSION,
    }
