from __future__ import annotations

import sqlite3

import pytest

import backend.application.tasks.batches as batch_module
from backend.application.tasks.batches import get_task_batch, list_task_batches
from backend.application.tasks.chain_outputs import acquired_chain_output_evidence
from backend.application.tasks.repository import TaskRepository
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage


pytestmark = pytest.mark.no_storage_bootstrap


def test_task_repository_passive_read_does_not_create_missing_database(tmp_path):
    db_path = tmp_path / "missing.sqlite3"
    repo = TaskRepository(str(db_path))

    assert not db_path.exists()
    with pytest.raises(sqlite3.OperationalError):
        repo.get("missing")
    assert not db_path.exists()


def test_bootstrapped_task_reads_avoid_writable_repository_connection(tmp_path, monkeypatch):
    initialize_runtime_storage(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)

    def fail_writable(*args, **kwargs):
        raise AssertionError("writable task connection used by passive read")

    monkeypatch.setattr(TaskRepository, "_connect", fail_writable)
    assert repo.get("missing") is None
    assert repo.list_pending() == []
    assert repo.list_tasks() == []
    assert repo.list_due() == []


def test_batch_and_chain_reads_use_existing_database(tmp_path, monkeypatch):
    initialize_runtime_storage(tmp_path)

    def fail_raw_connect(*args, **kwargs):
        raise AssertionError("raw writable sqlite connection used by passive read")

    monkeypatch.setattr(batch_module, "connect", fail_raw_connect)
    assert list_task_batches(tmp_path) == []
    with pytest.raises(KeyError):
        get_task_batch(tmp_path, "missing")
    assert acquired_chain_output_evidence(tmp_path, "unknown")["card_ids"] == []
