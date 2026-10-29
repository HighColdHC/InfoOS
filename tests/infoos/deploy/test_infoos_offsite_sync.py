from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / 'deploy/docker/infoos-offsite-sync'
loader = importlib.machinery.SourceFileLoader('infoos_offsite_sync', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
offsite = importlib.util.module_from_spec(spec)
loader.exec_module(offsite)


def executable(path, code):
    path.write_text(f'#!{sys.executable}\n' + code)
    path.chmod(0o755)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(offsite, 'CONFIG', tmp_path / 'absent.env')
    for name in offsite.KEYS:
        monkeypatch.delenv('INFOOS_OFFSITE_' + name, raising=False)
    staging, destination, data, commands = (tmp_path / name for name in ('staging', 'destination', 'backup-data', 'bin'))
    data.mkdir()
    commands.mkdir()
    with sqlite3.connect(data / 'infoos.sqlite3') as conn:
        conn.execute('CREATE TABLE cards (id INTEGER)')
        conn.execute('INSERT INTO cards VALUES (1)')
    (data / 'archive').mkdir()
    with sqlite3.connect(data / 'archive/history.sqlite3') as conn:
        conn.execute('CREATE TABLE history (id INTEGER)')
    (commands / 'zstd').symlink_to(shutil.which('zstd'))
    executable(commands / 'docker', 'import json, os\nprint(json.dumps([{\n'
               '"Mounts": [{"Destination": "/var/lib/infoos/data", "Type": "bind", "Source": os.environ["FAKE_DATA"]}],\n'
               '"Config": {"Labels": {"org.opencontainers.image.revision": "test"}}}]))\n')
    executable(commands / 'rsync', '''import os, sys, shutil
from pathlib import Path
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write('rsync ' + repr(sys.argv[1:]) + '\\n')
if os.environ.get('FAIL_RSYNC') == 'all' or (os.environ.get('FAIL_RSYNC') == 'retention' and '--delete-after' in sys.argv):
    sys.exit(23)
source, target = map(Path, sys.argv[-2:])
target.mkdir(parents=True, exist_ok=True)
for path in source.iterdir():
    output = target / path.name
    if output.exists():
        shutil.rmtree(output) if output.is_dir() else output.unlink()
    shutil.copytree(path, output) if path.is_dir() else shutil.copy2(path, output)
if '--delete-after' in sys.argv:
    for path in target.iterdir():
        if not (source / path.name).exists():
            shutil.rmtree(path) if path.is_dir() else path.unlink()
''')
    monkeypatch.setenv('PATH', str(commands))
    monkeypatch.setenv('FAKE_DATA', str(data))
    monkeypatch.setenv('CALL_LOG', str(tmp_path / 'calls'))
    monkeypatch.setenv('INFOOS_OFFSITE_STAGING', str(staging))
    monkeypatch.setenv('INFOOS_OFFSITE_RSYNC_DEST', str(destination))
    monkeypatch.setenv('INFOOS_OFFSITE_KEEP', '2')
    return staging, destination, commands


def test_missing_config_and_dry_run_has_no_side_effects(setup, monkeypatch, capsys):
    staging, destination, _ = setup
    monkeypatch.delenv('INFOOS_OFFSITE_RSYNC_DEST')
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), '--dry-run'])
    with pytest.raises(SystemExit) as exc:
        offsite.main()
    assert exc.value.code == 1
    assert 'INFOOS_OFFSITE_RSYNC_DEST' in capsys.readouterr().err
    monkeypatch.setenv('INFOOS_OFFSITE_RSYNC_DEST', 'user@example.invalid:/private/path/')
    monkeypatch.setenv('INFOOS_OFFSITE_AGE_RECIPIENT', 'age1test')
    offsite.main()
    output = capsys.readouterr().out
    assert 'create --db-only' in output and 'age -r' in output and 'example.invalid' in output
    assert '/private/path' not in output and 'user@' not in output
    assert not staging.exists() and not destination.exists()
    assert not Path(os.environ['CALL_LOG']).exists()


def test_age_required_does_not_fall_back_to_plaintext(setup, monkeypatch):
    staging, destination, _ = setup
    monkeypatch.setenv('INFOOS_OFFSITE_AGE_RECIPIENT', 'age1test')
    with pytest.raises(ValueError, match='required executable missing: age'):
        offsite.sync(offsite.configuration())
    assert not staging.exists() and not destination.exists()


@pytest.mark.parametrize('encrypted', [False, True])
def test_success_retention_json_and_failure_preserves_previous(setup, monkeypatch, capsys, encrypted):
    staging, destination, commands = setup
    if encrypted:
        monkeypatch.setenv('INFOOS_OFFSITE_AGE_RECIPIENT', 'age1test')
        executable(commands / 'age', '''import sys, tarfile
from pathlib import Path
with tarfile.open(sys.argv[-1]) as archive:
    assert any(p.name.endswith('manifest.json') for p in archive.getmembers())
    assert not any('/assets/' in p.name for p in archive.getmembers())
Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'fake ciphertext')
''')
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT)])
    suffix = 'encrypted' if encrypted else 'plain'
    sent = staging / ('sent-' + suffix)
    remote = destination / ('infoos-db-only-' + suffix)
    for _ in range(3):
        offsite.main()
        summary = json.loads(capsys.readouterr().out)
        assert set(summary) == {'time', 'snapshot', 'bytes', 'encrypted', 'destination_host'}
        assert summary['encrypted'] is encrypted and summary['destination_host'] == 'local'
        assert summary['bytes'] > 0 and summary['snapshot'].startswith('infoos-backup-')
    assert len(list(sent.iterdir())) == len(list(remote.iterdir())) == 2
    assert len(list((staging / 'snapshots').iterdir())) == 2
    if encrypted:
        assert all(p.suffix == '.age' for p in remote.iterdir())
        assert 'snapshot.tar' not in Path(os.environ['CALL_LOG']).read_text()
    else:
        for path in remote.iterdir():
            assert not (path / 'assets').exists()
    previous = {p.name: p.stat().st_mtime_ns for p in sent.iterdir()}
    previous_remote = {p.name for p in remote.iterdir()}
    monkeypatch.setenv('FAIL_RSYNC', 'all')
    with pytest.raises(RuntimeError, match='rsync upload failed'):
        offsite.sync(offsite.configuration())
    assert {p.name: p.stat().st_mtime_ns for p in sent.iterdir()} == previous
    assert {p.name for p in remote.iterdir()} == previous_remote
    assert len(list((staging / 'snapshots').iterdir())) == 2
    monkeypatch.setenv('FAIL_RSYNC', 'retention')
    with pytest.raises(RuntimeError, match='rsync retention failed'):
        offsite.sync(offsite.configuration())
    assert previous.keys() <= {p.name for p in sent.iterdir()}
    assert previous_remote <= {p.name for p in remote.iterdir()}
