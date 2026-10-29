from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from backend.infrastructure.storage import database
from backend.infrastructure.storage.migrations.runner import DatabaseNotReady, migrate_database
import pytest
import sqlite3


def test_importing_app_main_does_not_create_default_database(tmp_path: Path) -> None:
    repo_root = Path(__file__).parents[3]
    data_home = tmp_path / "data-home"
    state_home = tmp_path / "state-home"
    env = os.environ.copy()
    env.update(
        {
            "INFOOS_DATA_HOME": str(data_home),
            "INFOOS_STATE_HOME": str(state_home),
            "INFOOS_LLM_CONFIG": str(state_home / "config" / "llm.json"),
            "PYTHONPATH": str(repo_root),
        }
    )

    completed = subprocess.run(
        [sys.executable, "-c", "import backend.app.main"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not (data_home / "infoos.sqlite3").exists()


@pytest.mark.no_storage_bootstrap
def test_ensure_database_initialized_is_readonly_and_rechecks_replaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "data" / "infoos.sqlite3"
    with pytest.raises(DatabaseNotReady):
        database.ensure_database_initialized(db_path)
    assert not db_path.exists()
    migrate_database(db_path)
    reads: list[Path] = []
    original = database.connect_readonly

    def counted_readonly(path: Path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(database, "connect_readonly", counted_readonly)
    first = database.ensure_database_initialized(db_path)
    second = database.ensure_database_initialized(db_path.parent / "." / db_path.name)
    assert first == db_path.resolve()
    assert second == first
    assert len(reads) == 1
    db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        conn.execute("pragma user_version=1")
    with pytest.raises(DatabaseNotReady):
        database.ensure_database_initialized(db_path)
