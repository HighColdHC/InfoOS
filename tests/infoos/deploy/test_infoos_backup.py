from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import sqlite3
import shutil

import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "deploy/docker/infoos-backup"
loader = importlib.machinery.SourceFileLoader("infoos_backup", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
backup = importlib.util.module_from_spec(spec)
loader.exec_module(backup)


@pytest.fixture(autouse=True)
def isolate_host_media_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.deployment, 'BACKUP_MEDIA_POLICY', tmp_path / 'unconfigured-policy.json')


def test_create_verify_incremental_and_prune(tmp_path):
    data = tmp_path / "backup-source"
    (data / "assets").mkdir(parents=True)
    with sqlite3.connect(data / "infoos.sqlite3") as conn:
        conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO cards(title) VALUES ('first')")
        conn.execute("PRAGMA user_version=6")
    (data / "assets" / "photo.bin").write_bytes(b"stable asset")
    root = tmp_path / "offsite"

    first = backup.create(root, data, "test-commit")
    backup.verify(first)
    assert (first.stat().st_mode & 0o777) == 0o700
    assert backup.verified_manifest(first)["table_counts"]["cards"] == 1

    second = backup.create(root, data, "test-commit")
    backup.verify(second)
    assert (first / "assets/photo.bin").stat().st_ino == (second / "assets/photo.bin").stat().st_ino

    unrelated = root / "other"
    unrelated.mkdir()
    (root / "infoos-backup-unmanaged").mkdir()
    removed = backup.prune(root, 1)
    assert removed == [first]
    assert second.is_dir() and unrelated.is_dir() and (root / "infoos-backup-unmanaged").is_dir()

    (second / "assets/photo.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="backup file mismatch"):
        backup.verify(second)


def test_archive_online_backup_verify_and_incremental_link(tmp_path):
    data = tmp_path / "backup-source"
    (data / "assets").mkdir(parents=True)
    archive = data / "archive/task_events/task_events-2026-09.sqlite3"
    archive.parent.mkdir(parents=True)
    with sqlite3.connect(data / "infoos.sqlite3") as conn:
        conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY)")
    with sqlite3.connect(archive) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("PRAGMA user_version=2")
        conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO task_events VALUES (1)")
        conn.commit()
        root = tmp_path / "offsite"
        first = backup.create(root, data, "test-commit")
        backup.verify(first)
        key = "archive/task_events/task_events-2026-09.sqlite3.zst"
        manifest = backup.verified_manifest(first)
        assert manifest["archives"][key]["user_version"] == 2
        assert manifest["archives"][key]["table_counts"] == {"task_events": 1}
        assert manifest["archives"][key]["sha256"]
        assert manifest["files"][key]["sha256"]

        second = backup.create(root, data, "test-commit")
        backup.verify(second)
        assert (first / key).stat().st_ino == (second / key).stat().st_ino

    (second / key).write_bytes(b"tampered archive")
    with pytest.raises(ValueError, match="backup file mismatch"):
        backup.verify(second)


def test_archive_rejects_non_sqlite_file(tmp_path):
    data = tmp_path / "backup-source"
    (data / "assets").mkdir(parents=True)
    (data / "archive").mkdir()
    (data / "archive" / "unexpected.txt").write_text("unexpected")
    with sqlite3.connect(data / "infoos.sqlite3"):
        pass
    with pytest.raises(ValueError, match="unsupported file in archive"):
        backup.create(tmp_path / "offsite", data, "test-commit")


def test_prune_rejects_keep_zero(tmp_path):
    root = tmp_path / "offsite"
    root.mkdir()
    with pytest.raises(ValueError, match="--keep must be at least 1"):
        backup.prune(root, 0)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "prune", "--dest", str(root), "--keep", "0"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "--keep must be at least 1" in result.stderr


def test_db_only_cli_scope_retention_and_legacy_manifest(tmp_path):
    import json

    data = tmp_path / 'source'
    (data / 'assets').mkdir(parents=True)
    (data / 'assets/large.bin').write_bytes(b'not backed up')
    (data / 'archive').mkdir()
    for path in (data / 'infoos.sqlite3', data / 'archive/history.sqlite3'):
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE records (id INTEGER)')
            conn.execute('INSERT INTO records VALUES (1)')
    root = tmp_path / 'backups'
    full_old = backup.create(root, data, 'test')
    # Old manifests remain verifiable and belong to the full retention group.
    manifest_path = full_old / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    del manifest['scope']
    manifest_path.write_text(json.dumps(manifest))
    backup.verify(full_old)
    small = []
    for _ in range(2):
        result = subprocess.run([sys.executable, str(SCRIPT), 'create', '--db-only', '--dest', str(root),
                                 '--data-dir', str(data)], capture_output=True, text=True, check=True)
        snapshot = Path(result.stdout.strip())
        subprocess.run([sys.executable, str(SCRIPT), 'verify', '--dest', str(snapshot)], check=True)
        assert not (snapshot / 'assets').exists()
        manifest = backup.verified_manifest(snapshot)
        assert manifest['scope'] == 'db-only'
        assert set(manifest['files']) == {'infoos.sqlite3.zst', 'archive/history.sqlite3.zst'}
        small.append(snapshot)
    backup.prune(root, 1)
    assert full_old.exists() and small[1].exists() and not small[0].exists()
    full_new = backup.create(root, data, 'test')
    subprocess.run([sys.executable, str(SCRIPT), 'prune', '--dest', str(root), '--keep', '1'], check=True)
    assert not full_old.exists() and full_new.exists() and small[1].exists()
    # db-only must work even when assets is absent.
    shutil.rmtree(data / 'assets')
    backup.verify(backup.create(root, data, 'test', db_only=True))
