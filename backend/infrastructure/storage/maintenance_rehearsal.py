"""Standalone copy-only rehearsal measurements and report assembly (no runtime imports)."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time


def read_json(path: Path):
    return json.loads(path.read_text())


def unpack(path: Path, expected_bytes: int) -> dict:
    with path.open('xb') as target:
        shutil.copyfileobj(sys.stdin.buffer, target, 1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    size = path.stat().st_size
    if size != expected_bytes:
        raise ValueError(f'decompressed size differs: expected {expected_bytes}, got {size}')
    return {'database_bytes': size, 'source': 'host zstd stream; no recovery/live volume mounted'}


def check_receipt(path: Path) -> dict:
    receipt = read_json(path)
    if receipt.get('backup_format') != 'sqlite-zstd-v1':
        raise ValueError('receipt backup_format must be sqlite-zstd-v1')
    for key in ('revision', 'image_id', 'volumes', 'sqlite_recovery'):
        if not receipt.get(key):
            raise ValueError(f'receipt missing {key}')
    # Receipt describes the deployment, not necessarily the revision of backed-up data.
    # It contains no decompressed byte count or digest; zstd CRC + full integrity follow.
    return {'receipt': receipt, 'note': 'No assets or cold archives in SQLite recovery point; '
            'receipt revision/image describe deployment, not proof of DB schema compatibility.'}


def stats(path: Path, baseline: Path | None = None) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('pragma query_only=on')
        conn.execute('begin')
        integrity = [row[0] for row in conn.execute('pragma integrity_check')]
        if integrity != ['ok']:
            raise ValueError(f'integrity_check failed: {integrity}')
        result = {key: conn.execute(f'pragma {key}').fetchone()[0]
                  for key in ('page_size', 'page_count', 'freelist_count')}
        result.update(database_bytes=path.stat().st_size, integrity_check=integrity)
        result['free_bytes'] = result['page_size'] * result['freelist_count']
        objects, dbstat_error = {}, None
        try:
            objects = dict(conn.execute('select name, sum(pgsize) from dbstat group by name'))
        except sqlite3.OperationalError as exc:
            dbstat_error = str(exc)
        # PRAGMA table_list excludes FTS virtual/shadow tables from business row checks.
        tables = sorted(row[1] for row in conn.execute('pragma table_list')
                        if row[0] == 'main' and row[2] == 'table' and not row[1].startswith('sqlite_'))
        counts, sizes = {}, {}
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            counts[name] = conn.execute(f'select count(*) from {quoted}').fetchone()[0]
            indexes = [row[0] for row in conn.execute(
                "select name from sqlite_master where type='index' and tbl_name=?", (name,))]
            sizes[name] = {'rows': counts[name], 'table_bytes': objects.get(name) if objects else None,
                           'index_bytes': sum(objects.get(i, 0) for i in indexes) if objects else None}
        result.update(row_counts=counts, tables=sizes, object_page_bytes=objects,
                      size_method='dbstat allocated pages, not logical payload or subset reclaim estimates',
                      dbstat_error=dbstat_error)
        if baseline is not None and counts != read_json(baseline)['row_counts']:
            raise ValueError('business table row counts changed since baseline')
    media = path.parent / 'assets/raw-media'
    files = [p for p in media.rglob('*') if p.is_file()] if media.exists() else []
    result['raw_media'] = {'files': len(files), 'bytes': sum(p.stat().st_size for p in files),
                           'allocated_bytes': sum(p.stat().st_blocks * 512 for p in files)}
    result['row_counts_match_baseline'] = True if baseline is not None else None
    return result


def finish_report(work: Path, report: Path, exit_code: int, failed_step: str) -> dict:
    context = read_json(work / '.rehearsal/context.json')
    steps = []
    for meta in sorted((work / '.rehearsal').glob('*.meta.json')):
        step = read_json(meta)
        if step['exit_code'] is None:
            step.update(ended_ns=time.time_ns(), exit_code=exit_code, interrupted=True)
        step['wall_seconds'] = round((step['ended_ns'] - step['started_ns']) / 1e9, 3)
        for stream in ('stdout', 'stderr'):
            file = meta.with_name(meta.name.replace('.meta.json', '.' + stream))
            raw = file.read_text(errors='replace') if file.exists() else ''
            try:
                step[stream + '_json'] = json.loads(raw)
            except ValueError:
                step[stream + '_json'] = None
            step[stream] = raw
        steps.append(step)
    outputs = {s['name']: s['stdout_json'] or {} for s in steps}
    before, after = outputs.get('baseline', {}), outputs.get('final', {})
    inventory = outputs.get('inventory', {})
    fts = outputs.get('fts', {})
    db_before, db_after = before.get('database_bytes'), after.get('database_bytes')
    reclaimed = db_before - db_after if db_before is not None and db_after is not None else None
    media = after.get('raw_media')
    words = list((fts.get('sample_after') or {}).keys())
    summary = {
        'database_bytes_before': db_before, 'database_bytes_after': db_after,
        'database_reclaimed_bytes': reclaimed, 'raw_media': media,
        'net_reclaimed_bytes_db_minus_new_media': reclaimed - media['bytes'] if reclaimed is not None and media else None,
        'review_04': {key: inventory.get(key) for key in (
            'retired_terminal', 'superseded_terminal', 'safe_delete_candidates_30d', 'reference_scan',
            'exclusions_first_match', 'finalize')},
        'fts': {'integrity_check': fts.get('integrity_check'), 'counts': fts.get('after', {}).get('fts'),
                'sample_before': fts.get('sample_before'), 'sample_after': fts.get('sample_after'),
                'sample_stable': fts.get('sample_before') == fts.get('sample_after') if fts else None,
                'keywords': words, 'three_keywords_available': len(words) == 3,
                'keywords_with_hits': [w for w in words if any(v['count'] for v in fts['sample_after'][w].values())],
                'wal_checkpoint': fts.get('wal_checkpoint')},
        'final_integrity_check': after.get('integrity_check'),
        'row_counts_match_baseline': after.get('row_counts_match_baseline'),
    }
    result = {**context, 'status': 'completed' if exit_code == 0 else 'failed', 'exit_code': exit_code,
              'failed_step': failed_step or None,
              'work_dir_kept': context.get('keep', False) or (work / '.rehearsal/retained.txt').exists(),
              'failure_reason': (work / '.rehearsal/failure.txt').read_text().strip()
                  if (work / '.rehearsal/failure.txt').exists() else None,
              'total_wall_seconds': round((time.time_ns() - context['started_ns']) / 1e9, 3),
              'steps': steps, 'summary': summary,
              'limitations': ['Copy-only rehearsal; no live data/services touched.',
                  'SQLite recovery has no cold event archives: review-04 counts are main-DB-only upper bounds, not deletion approval.',
                  'No tasks deleted; reclamation excludes any hypothetical review-04 deletion.',
                  'Existing sha256 media references may require assets absent from SQLite recovery; stop on missing files, never borrow live assets.',
                  'Offline timings exclude real service stop/start, fresh backup verification, revision-check and bounded product acceptance.',
                  'Total includes preflight/decompression/measurement and Docker startup; cache and concurrent host load affect timings.']}
    temporary = report.with_name(report.name + '.tmp')
    with temporary.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temporary.replace(report)
    return {'report': str(report)}
