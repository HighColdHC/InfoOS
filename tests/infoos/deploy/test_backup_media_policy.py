from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from backend.infrastructure.storage.repositories import CardContentBlockRepository
from tests.infoos._factories import setup_card_and_asset


SCRIPT = Path(__file__).resolve().parents[3] / 'deploy/docker/infoos-backup'
loader = importlib.machinery.SourceFileLoader('speech_backup', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
backup = importlib.util.module_from_spec(spec)
loader.exec_module(backup)
RELATIVE = 'card/generic/speech.mp4'
TRANSCRIPT = 'This transcript was reviewed and approved.'


@pytest.fixture
def approved_media(tmp_path, monkeypatch):
    # Use the real migrated schema and repositories, including assets-relative paths.
    data = tmp_path / 'data'
    speech = data / 'assets' / RELATIVE
    speech.parent.mkdir(parents=True)
    speech.write_bytes(b'approved speech')
    (data / 'assets/visual.mp4').write_bytes(b'visual demonstration')
    setup_card_and_asset(tmp_path, 'card', 'video', RELATIVE, asset_id='voice')
    CardContentBlockRepository(tmp_path).upsert_block({
        'block_id': 'transcript', 'card_id': 'card', 'layer': 'derived',
        'block_kind': 'video_transcript', 'status': 'enriched', 'body': TRANSCRIPT,
        'source_url': 'asset:voice', 'provenance': 'video_transcribe',
    })
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'format': 'infoos-speech-media-exclusions-v1',
        'approval': 'test fixture', 'files': {RELATIVE: {
            'size': speech.stat().st_size,
            'sha256': hashlib.sha256(speech.read_bytes()).hexdigest(),
            'transcript_sha256': hashlib.sha256(TRANSCRIPT.encode()).hexdigest(),
        }}}))
    monkeypatch.setattr(backup.deployment, 'BACKUP_MEDIA_POLICY', policy)
    return data, speech, policy


def test_selective_create_verify_restore_and_independent_retention(approved_media, tmp_path, monkeypatch):
    data, speech, policy = approved_media
    root = tmp_path / 'backups'
    monkeypatch.setattr(backup.deployment, 'BACKUP_MEDIA_POLICY', tmp_path / 'no-policy')
    full = backup.create(root, data, 'fixture')
    monkeypatch.setattr(backup.deployment, 'BACKUP_MEDIA_POLICY', policy)
    first = backup.create(root, data, 'fixture')
    selective = backup.create(root, data, 'fixture')
    backup.verify(selective)
    manifest = backup.verified_manifest(selective)
    assert manifest['media_scope'] == 'selective'
    assert set(manifest['excluded_media']) == {RELATIVE}
    assert not (selective / 'assets' / RELATIVE).exists()
    assert speech.read_bytes() == (full / 'assets' / RELATIVE).read_bytes() == b'approved speech'
    assert (selective / 'assets/visual.mp4').stat().st_ino == (full / 'assets/visual.mp4').stat().st_ino

    restored = tmp_path / 'restored'
    shutil.copytree(selective / 'assets', restored / 'assets')
    backup.decompress_database(selective / backup.DATABASE, restored / 'infoos.sqlite3')
    assert backup.database_facts(restored / 'infoos.sqlite3') == (manifest['user_version'], manifest['table_counts'])
    with sqlite3.connect(restored / 'infoos.sqlite3') as conn:
        assert conn.execute('select body from card_content_blocks').fetchone() == (TRANSCRIPT,)
    assert not (restored / 'assets' / RELATIVE).exists()
    assert (restored / 'assets/visual.mp4').read_bytes() == b'visual demonstration'

    assert backup.prune(root, 1) == [first]
    assert full.exists() and selective.exists()
    (selective / 'assets/visual.mp4').write_bytes(b'visual demonstratioX')
    with pytest.raises(ValueError, match='backup file mismatch'):
        backup.verify(selective)


@pytest.mark.parametrize('change', [
    'size', 'bytes', 'transcript', 'whitespace', 'failed', 'empty',
    'unrelated-block', 'missing-block', 'missing-asset', 'wrong-kind', 'wrong-path', 'no-policy',
])
def test_changed_or_missing_evidence_keeps_video(approved_media, tmp_path, change):
    data, speech, policy = approved_media
    cached = backup.deployment.backup_media_exclusions(data)
    assert set(cached) == {RELATIVE}
    if change == 'size':
        speech.write_bytes(b'longer replacement video')
    elif change == 'bytes':
        speech.write_bytes(b'X' * speech.stat().st_size)
    elif change == 'no-policy':
        policy.unlink()
    else:
        with sqlite3.connect(data / 'infoos.sqlite3') as conn:
            conn.execute({
                'transcript': "update card_content_blocks set body='replacement transcript'",
                'whitespace': "update card_content_blocks set body=body||' '",
                'failed': "update card_content_blocks set status='failed'",
                'empty': "update card_content_blocks set body=''",
                'unrelated-block': "update card_content_blocks set source_url='asset:another-video'",
                'missing-block': 'delete from card_content_blocks',
                'missing-asset': 'delete from card_assets',
                'wrong-kind': "update card_assets set asset_kind='image'",
                'wrong-path': "update card_assets set local_path='assets/'||local_path",
            }[change])
    assert backup.deployment.backup_media_exclusions(data, cached=cached) == {}
    snapshot = backup.create(tmp_path / 'backups', data, 'fixture')
    backup.verify(snapshot)
    assert (snapshot / 'assets' / RELATIVE).read_bytes() == speech.read_bytes()
    assert backup.verified_manifest(snapshot)['media_scope'] == 'full'


@pytest.mark.parametrize('status', ['enriched', 'preserved', 'done'])
@pytest.mark.parametrize('absolute', [False, True])
def test_real_transcript_statuses_and_supported_asset_paths(approved_media, status, absolute):
    data, _, _ = approved_media
    with sqlite3.connect(data / 'infoos.sqlite3') as conn:
        conn.execute('update card_content_blocks set status=?', (status,))
        if absolute:
            conn.execute('update card_assets set local_path=?', ('/var/lib/infoos/data/assets/' + RELATIVE,))
    assert set(backup.deployment.backup_media_exclusions(data)) == {RELATIVE}


def test_exclusions_use_the_database_copy_being_backed_up(approved_media, tmp_path):
    data, _, _ = approved_media
    database = tmp_path / 'backup.sqlite3'
    backup.sqlite_backup(data / 'infoos.sqlite3', database)
    with sqlite3.connect(data / 'infoos.sqlite3') as conn:
        conn.execute('delete from card_content_blocks')
    assert backup.deployment.backup_media_exclusions(data) == {}
    assert set(backup.deployment.backup_media_exclusions(data, database=database)) == {RELATIVE}


@pytest.mark.parametrize('change', ['format', 'files', 'traversal', 'alias', 'glob', 'size', 'hash', 'missing-hash'])
def test_policy_boundary_rejects_invalid_format_paths_and_evidence(approved_media, change):
    data, _, policy = approved_media
    content = json.loads(policy.read_text())
    entry = content['files'][RELATIVE]
    if change == 'format':
        content['format'] = 'unknown'
    elif change == 'files':
        content['files'] = []
    elif change in {'traversal', 'alias', 'glob'}:
        content['files'] = {{'traversal': '../speech.mp4', 'alias': './speech.mp4', 'glob': '*.mp4'}[change]: entry}
    elif change == 'size':
        entry['size'] = True
    elif change == 'hash':
        entry['sha256'] = 'invalid'
    else:
        del entry['transcript_sha256']
    policy.write_text(json.dumps(content))
    with pytest.raises(ValueError):
        backup.deployment.backup_media_exclusions(data)


@pytest.mark.parametrize('format', ['linked-files-selective-v1', 'selective-media-pruned-v1'])
def test_full_sync_after_selective_link_dest_recovers_excluded_video(approved_media, tmp_path, format):
    data, speech, _ = approved_media
    assets = data / 'assets'
    root = tmp_path / 'snapshots'
    previous = root / '20261027'
    for name in ('state', *(['archive'] if format == 'linked-files-selective-v1' else [])):
        (previous / name).mkdir(parents=True)
    backup.sqlite_backup(data / 'infoos.sqlite3', previous / 'infoos.sqlite3')
    volumes = {'/var/lib/infoos/data': 'data', '/var/lib/infoos/state': 'state'}
    (previous / 'receipt.json').write_text(json.dumps({'backup_format': format, 'volumes': volumes}))
    cached = backup.deployment.backup_media_exclusions(data)
    backup.deployment.sync_snapshot(assets, previous / 'assets', None)
    backup.deployment.sync_snapshot(assets, previous / 'assets', None, media_exclusions=cached)
    assert not (previous / 'assets' / RELATIVE).exists()
    assert backup.deployment.latest_linked_backup(root, volumes) == previous

    pending = root / '.pending-next' / 'assets'
    backup.deployment.sync_snapshot(assets, pending, previous / 'assets', media_exclusions=cached)
    assert not (pending / RELATIVE).exists()
    with sqlite3.connect(data / 'infoos.sqlite3') as conn:
        conn.execute("update card_content_blocks set body='no longer approved'")
    exclusions = backup.deployment.backup_media_exclusions(data, cached=cached)
    assert exclusions == {}
    backup.deployment.sync_snapshot(assets, pending, previous / 'assets', media_exclusions=exclusions)
    assert (pending / RELATIVE).read_bytes() == speech.read_bytes()
    assert not (previous / 'assets' / RELATIVE).exists()
    assert (pending / 'visual.mp4').stat().st_ino == (previous / 'assets/visual.mp4').stat().st_ino
