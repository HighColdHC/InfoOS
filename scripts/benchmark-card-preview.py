#!/usr/bin/env python3
"""Measure card preview query and complete read-model medians on an isolated DB.

Use the same script with --code-root <v14 checkout> to import the baseline code.
No schema or data is changed unless --build-synthetic is explicitly supplied.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time


def build_synthetic(path: Path, count: int) -> None:
    from backend.domain.timestamps import normalize_timestamp, timestamp_sort_key
    from backend.infrastructure.storage.migrations import migrate_database

    if path.exists():
        raise ValueError('--build-synthetic requires a new database path')
    migrate_database(path)
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with sqlite3.connect(path) as conn:
        version = conn.execute('pragma user_version').fetchone()[0]
        for first in range(0, count, 500):
            cards, raws, blocks = [], [], []
            for n in range(first, min(first + 500, count)):
                key = f'card-{n:08d}'
                stamp = (start + timedelta(days=n % 90, seconds=n % 86400)).isoformat().replace('+00:00', 'Z')
                published = None if n % 13 == 0 else stamp
                content = ('Ordinary source material. 日常信息与产品记录。' * 24)
                body = ('Additional source evidence. 附加资料与观察记录。' * 16)
                for residue, word in [(0, 'nebula'), (2, '人工智能'), (4, '研究')]:
                    if n % 10 == residue:
                        content += ' ' + word
                    if n % 10 == residue + 1:
                        body += ' ' + word
                raws.append((key, stamp, stamp, stamp))
                card = (key, f'https://example.test/{key}', f'Observation 观察记录 {n}', content,
                        published, stamp, stamp, stamp, key)
                if version >= 15:
                    normalized = normalize_timestamp(published)
                    card += (timestamp_sort_key(published, stamp),
                             (normalized.replace('Z', '') + '.000000')[:26] if normalized else None)
                cards.append(card)
                blocks.append((key + '-body', key, 'article', body, stamp, stamp))
                blocks.append((key + '-gap', key, 'enrichment_gap', 'nebula 人工智能 研究', stamp, stamp))
            conn.executemany("""insert into raw_items(connector_id,raw_item_id,source_platform,source_type,
                source_url,captured_at,first_seen_at,last_seen_at) values ('bench',?,'rss','article','',?,?,?)""", raws)
            extra_columns = ',sort_ts,published_ts' if version >= 15 else ''
            extra_values = ',?,?' if version >= 15 else ''
            conn.executemany(f"""insert into cards(card_id,source_platform,source_type,source_url,
                title,content,published_at,captured_at,processed_at,updated_at,raw_connector_id,raw_item_id,
                status,version,content_hash,completeness_status{extra_columns})
                values (?,'rss','article',?,?,?,?,?,?,?,'bench',?,'active',1,'sha256:bench','complete'{extra_values})""", cards)
            conn.executemany("""insert into card_content_blocks(block_id,card_id,layer,block_kind,body,
                status,created_at,updated_at) values (?,?,'original',?,?,'preserved',?,?)""", blocks)
            if version < 15:
                conn.executemany("""insert into infoos_search(entity_type,entity_id,title,body,source)
                    select 'card',card_id,title,content || ' ' || summary,source_author from cards where card_id=?""",
                    [(r[0],) for r in cards])
            conn.commit()
        conn.execute('analyze')


def measure(fn, repeats):
    for _ in range(2):
        result = fn()
    timings = []
    for _ in range(repeats):
        started = time.perf_counter()
        result = fn()
        timings.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(timings), 3), result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--build-synthetic', type=int, metavar='N')
    parser.add_argument('--code-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--repeats', type=int, default=9)
    args = parser.parse_args()
    if args.repeats < 1 or (args.build_synthetic is not None and args.build_synthetic < 1):
        parser.error('repeats and synthetic count must be positive')
    sys.path.insert(0, str(args.code_root.resolve()))
    path = args.db.expanduser().resolve()
    if args.build_synthetic is not None:
        build_synthetic(path, args.build_synthetic)
    from backend.infrastructure.storage import base
    from backend.infrastructure.storage.card_preview_query import read_preview_rows
    from backend.read_models.card_preview import read_card_preview_page
    # All benchmark repositories resolve the explicit --db, never a user home.
    base.default_database_path = lambda _base: path
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as conn:
        version = conn.execute('pragma user_version').fetchone()[0]
        count = conn.execute('select count(*) from cards').fetchone()[0]
        block_count = conn.execute('select count(*) from card_content_blocks').fetchone()[0]
    print(json.dumps(dict(sqlite=sqlite3.sqlite_version, schema=version, cards=count, blocks=block_count,
                          repeats=args.repeats, code_root=str(args.code_root.resolve())), ensure_ascii=False), flush=True)
    common = dict(query=None, platform=None, completeness=None, status='active', sort='published_desc', limit=20, offset=0)
    scenarios = [
        ('default', {}), ('english', {'query': 'nebula'}),
        ('chinese_long', {'query': '人工智能'}), ('chinese_short', {'query': '研究'}),
        ('published_range', {'published_since': datetime(2026, 8, 1, tzinfo=timezone.utc),
                             'published_until': datetime(2026, 8, 7, tzinfo=timezone.utc)}),
    ]
    for name, filters in scenarios:
        params = {**common, **filters}
        query_ms, (cards, total) = measure(lambda: read_preview_rows(Path('.'), **params), args.repeats)
        page_ms, page = measure(lambda: read_card_preview_page(Path('.'), **params), args.repeats)
        assert page['total'] == total
        ids = [r['card_id'] for r in cards]
        assert ids == [r['card_id'] for r in page['items']]
        fingerprint = hashlib.sha256(json.dumps([total, ids]).encode()).hexdigest()[:16]
        print(json.dumps(dict(scenario=name, rows_median_ms=query_ms, page_median_ms=page_ms,
                              total=total, result_hash=fingerprint), ensure_ascii=False), flush=True)
    print('benchmark complete', flush=True)


if __name__ == '__main__':
    main()
