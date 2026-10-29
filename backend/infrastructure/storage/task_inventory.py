#!/usr/bin/env python3
"""Standalone read-only task inventory; no InfoOS imports, schema setup or writes.

Usage: python3 scripts/infoos-task-inventory.py /path/to/infoos.sqlite3 [--now ISO8601]
For zero filesystem changes, mount DB/WAL/SHM and cold archives read-only;
SQLite mode=ro alone can still update read-lock marks in a writable SHM file.
Candidate means review only: retired AND superseded, no references/lineage,
success/skipped, all activity older than 30 days. Live current input hashes are
not inferred from task ordering. Active types are always excluded.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import time

# Snapshot of backend/domain/tasks.py RETIRED_TASK_TYPES (verified 2026-10-21).
# Deliberately frozen in this standalone review tool; not a deletion policy.
RETIRED = frozenset({
    'semantic_extract', 'score', 'decide', 'output_generate', 'topic_update',
    'action_execute', 'cluster', 'daily_brief', 'card_llm_enrich',
    'image_understand', 'summary', 'context_pack',
})
TERMINAL = frozenset({'success', 'skipped', 'failed', 'blocked'})
JSON_COLUMNS = {
    'acquisition_checkpoints': ('payload',),
    'run_history': ('derived_task_ids',),
    'task_batches': ('metadata',),
    'task_events': ('payload',),
    'task_event_archive': ('payload',),
    'source_calibrations': ('raw_payload',),
    'auth_recovery_incidents': ('verification_context',),
}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def timestamp(value):
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # Naive historical timestamps follow InfoOS's UTC storage convention.
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def strings(value):
    """All JSON string values, including opaque/nested historical task pointers."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def logical_size(row):
    # UTF-8 cell bytes + approximate record header/cell overhead; not file bytes.
    return 32 + sum(len(v.encode('utf-8')) if isinstance(v, str)
                    else len(v) if isinstance(v, bytes)
                    else 8 if v is not None else 0 for v in row)


def inventory(path, now, max_seconds, archive_dir=None):
    started = time.monotonic()
    cutoff = (now - timedelta(days=30)).timestamp()

    def tick():
        if time.monotonic() - started > max_seconds:
            raise TimeoutError('inventory exceeded --max-seconds; no partial candidate result emitted')

    # Explicit mode=ro; never immutable=1 (must see live WAL). No file creation fallback.
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True,
                           isolation_level=None, timeout=5)
    conn.row_factory = sqlite3.Row
    # Deny all write/DDL/attach operations, including writable PRAGMAs.
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
               sqlite3.SQLITE_RECURSIVE}

    def authorize(action, arg1, arg2, database, trigger):
        if action in allowed:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and arg1 in {'table_info', 'foreign_key_list'}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'BEGIN':
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    # dbstat may need internal virtual-table initialization; warm it read-only
    # before installing the authorizer (SQLite can emit internal SQLITE_UPDATE).
    dbstat_error = None
    try:
        conn.execute('select name from dbstat limit 1').fetchone()
    except sqlite3.OperationalError as exc:
        dbstat_error = str(exc)
    conn.set_authorizer(authorize)
    conn.set_progress_handler(lambda: int(time.monotonic() - started > max_seconds), 10000)
    try:
        conn.execute('begin')  # one consistent read snapshot, released on close
        schema = {}
        tables = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' and lower(coalesce(sql, '')) not like 'create virtual table%'")]
        for table in tables:
            schema[table] = {r['name'] for r in conn.execute('pragma table_info(' + quote(table) + ')')}
        required = {'task_id', 'task_type', 'target_kind', 'target_id', 'status',
                    'input_version', 'created_at', 'updated_at', 'finished_at',
                    'artifacts_summary', 'depends_on'}
        if not required <= schema.get('tasks', set()):
            raise ValueError('tasks schema missing required columns: ' + ', '.join(sorted(required - schema.get('tasks', set()))))

        # One sequential table scan. Store only compact identity/version/time data.
        rows = {}
        latest = {}  # group -> (newest UTC creation time, tied versions)
        sql_latest = {}  # existing projections order raw created_at strings
        uncertain_groups = set()
        by_status, by_type, by_pair = Counter(), Counter(), Counter()
        total_weight = 0
        retired_counts, retired_weights = Counter(), Counter()
        for row in conn.execute('select * from tasks'):
            tick()
            task_id, kind, status = row['task_id'], row['task_type'], row['status']
            group = (row['target_kind'], row['target_id'], kind)
            created = timestamp(row['created_at'])
            dates = [timestamp(row[c]) for c in ('created_at', 'updated_at', 'finished_at')]
            aged = all(d is not None and d < cutoff for d in dates)
            weight = logical_size(row)
            rows[task_id] = (kind, status, group, row['input_version'], created, aged, weight)
            if created is None or not row['input_version']:
                uncertain_groups.add(group)
            else:
                previous = latest.get(group)
                if previous is None or created > previous[0]:
                    latest[group] = (created, {row['input_version']})
                elif created == previous[0]:
                    previous[1].add(row['input_version'])
                lexical = sql_latest.get(group)
                if lexical is None or row['created_at'] > lexical[0]:
                    sql_latest[group] = (row['created_at'], {row['input_version']})
                elif row['created_at'] == lexical[0]:
                    lexical[1].add(row['input_version'])
            by_status[status] += 1
            by_type[kind] += 1
            by_pair[(status, kind)] += 1
            total_weight += weight
            if kind in RETIRED and status in TERMINAL:
                retired_counts[kind] += 1
                retired_weights[kind] += weight

        protected = set()
        protection_counts = Counter()
        malformed = Counter()
        finalize_counts, finalize_parents = Counter(), Counter()
        feishu_tasks = set()
        finalize_edges = []

        def decode(raw, label, expected):
            try:
                value = json.loads(raw) if raw is not None else expected()
                if not isinstance(value, expected):
                    raise ValueError('unexpected JSON shape')
                return value
            except (ValueError, TypeError, RecursionError):
                malformed[label] += 1
                return expected()

        def protect(values, label):
            found = {value for value in values if value in rows}
            protected.update(found)
            protection_counts[label] += len(found)
            return found

        # Second streamed scan: arbitrary nested JSON references matched by exact
        # identity. Protect both ends of an edge, even between candidate rows.
        for row in conn.execute('select task_id, task_type, depends_on, artifacts_summary from tasks'):
            tick()
            deps = decode(row['depends_on'], 'tasks.depends_on', list)
            artifacts = decode(row['artifacts_summary'], 'tasks.artifacts_summary', dict)
            refs = protect(strings([deps, artifacts]), 'tasks_json_reference_occurrences')
            if refs or deps or any(artifacts.get(k) for k in (
                    'chain_id', 'capture_id', 'chain_root_task_id', 'upstream_task_id',
                    'downstream_task_ids', 'upstream_media_tasks', 'media_gaps')):
                protected.add(row['task_id'])
                protection_counts['tasks_with_lineage_or_edges'] += 1
            if artifacts.get('feishu_backfill'):
                feishu_tasks.add(row['task_id'])
            if row['task_type'] == 'card_finalize':
                if artifacts.get('agent_evaluation_finalize'):
                    category = 'agent_evaluation'
                elif artifacts.get('opportunity_breakdown_finalize'):
                    category = 'opportunity_breakdown'
                elif artifacts.get('verification_finalize'):
                    category = 'user_verification'
                elif artifacts.get('maintenance_reason') == 'x_media_cleanup':
                    category = 'x_media_cleanup'
                elif artifacts.get('upstream_task_id'):
                    category = 'pipeline_parent_recorded_origin_unknown'
                else:
                    category = 'unattributed'
                finalize_counts[category] += 1
                parent = artifacts.get('upstream_task_id')
                if parent:
                    parent_kind = rows[parent][0] if parent in rows else 'missing_parent'
                    finalize_parents[parent_kind] += 1
                    finalize_edges.append((parent, parent_kind))

        # Discover named task pointers AND every declared FK to tasks, regardless
        # of foreign_keys setting. Scan each external table's relevant columns
        # once; no N-by-N JSON joins or per-task subqueries on a 3.6 GB database.
        coverage = {}
        for table, columns in schema.items():
            if table == 'tasks':
                continue
            direct = {c for c in columns if c == 'task_id' or c.endswith('_task_id')}
            arrays = {c for c in columns if c == 'task_ids' or c.endswith('_task_ids')}
            for fk in conn.execute('pragma foreign_key_list(' + quote(table) + ')'):
                if fk['table'] == 'tasks':
                    direct.add(fk['from'])
            json_cols = (set(JSON_COLUMNS.get(table, ())) & columns) | arrays
            selected = sorted(direct | json_cols)
            if not selected:
                continue
            coverage[table] = selected
            sql = 'select ' + ','.join(map(quote, selected)) + ' from ' + quote(table)
            for row in conn.execute(sql):
                tick()
                for column in selected:
                    value = row[column]
                    if value is None or value == '':
                        continue
                    label = table + '.' + column
                    if column in json_cols:
                        # JSON object or array; fail closed on malformed data.
                        try:
                            parsed = json.loads(value)
                            if not isinstance(parsed, (dict, list)):
                                raise ValueError('unexpected JSON shape')
                        except (ValueError, TypeError, RecursionError):
                            malformed[label] += 1
                            continue
                        protect(strings(parsed), label)
                    else:
                        protect([value], label)

        # Cold events live in separate monthly SQLite files, not a main-DB
        # task_event_archive table (see TaskEventArchiveStore). Keep their task
        # references too; never ATTACH or initialize an archive schema.
        archive_dir = archive_dir if archive_dir is not None else path.resolve().parent / 'archive' / 'task_events'
        archives = []
        for archive in sorted(archive_dir.glob('task_events-????-??.sqlite3')):
            tick()
            cold = sqlite3.connect(archive.resolve().as_uri() + '?mode=ro', uri=True,
                                   isolation_level=None, timeout=5)
            try:
                cold.set_authorizer(authorize)
                cold.set_progress_handler(lambda: int(time.monotonic() - started > max_seconds), 10000)
                cold.execute('begin')
                count = 0
                for task_id, payload in cold.execute('select task_id, payload from task_events'):
                    tick()
                    count += 1
                    protect([task_id], str(archive) + ':task_id')
                    parsed = decode(payload, str(archive) + ':payload', dict)
                    protect(strings(parsed), str(archive) + ':payload')
                archives.append({'path': str(archive), 'event_rows_scanned': count})
            finally:
                cold.close()

        old_counts, old_weights, exclusions = Counter(), Counter(), Counter()
        candidate_counts, candidate_weights = Counter(), Counter()
        for task_id, (kind, status, group, version, created, aged, weight) in rows.items():
            tick()
            current = latest.get(group)
            old = (group not in uncertain_groups and current is not None
                   and created < current[0] and version not in current[1])
            if old and status in TERMINAL:
                old_counts[kind] += 1
                old_weights[kind] += weight
            if status not in {'success', 'skipped'}:
                reason = 'failed_blocked_or_nonterminal'
            elif kind not in RETIRED:
                reason = 'active_or_unknown_type_current_inputs_not_recomputed'
            elif not old:
                reason = 'latest_or_ambiguous_version'
            elif version in sql_latest[group][1]:
                reason = 'latest_version_by_existing_sql_string_order'
            elif not aged:
                reason = 'within_30_days_or_missing_timestamp'
            elif task_id in protected:
                reason = 'referenced_or_has_lineage'
            elif malformed:
                reason = 'reference_scan_incomplete_invalid_json'
            else:
                candidate_counts[kind] += 1
                candidate_weights[kind] += weight
                continue
            exclusions[reason] += 1

        # Exact pages for whole tasks/index trees; subsets are weighted estimates.
        index_names = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='index' and tbl_name='tasks'")]
        names = ['tasks', *index_names]
        page_sizes = {}
        if dbstat_error is None:
            try:
                placeholders = ','.join('?' for _ in names)
                page_sizes = {r[0]: r[1] for r in conn.execute(
                    'select name, sum(pgsize) from dbstat where name in (' + placeholders + ') group by name', names)}
            except sqlite3.OperationalError as exc:
                dbstat_error = str(exc)
        table_bytes = page_sizes.get('tasks')
        index_bytes = sum(page_sizes.get(n, 0) for n in index_names) if page_sizes else None

        def estimate(counts, weights):
            count, weight = sum(counts.values()), sum(weights.values())
            return {'rows': count, 'by_type': dict(sorted(counts.items())),
                    'estimated_table_bytes': round(table_bytes * weight / total_weight) if table_bytes and total_weight else weight,
                    'estimated_index_bytes': round(index_bytes * count / len(rows)) if index_bytes is not None and rows else None,
                    'logical_row_bytes': weight}

        tick()  # dbstat fallback must not hide an exhausted total budget
        return {
            'database': str(path.resolve()), 'read_only_uri_mode': 'ro',
            'evaluated_at_utc': now.isoformat(), 'retention_days': 30,
            'cutoff_utc': datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'total_rows': len(rows), 'by_status': dict(sorted(by_status.items())),
            'by_type': dict(sorted(by_type.items())),
            'by_status_and_type': [{'status': s, 'task_type': t, 'rows': n}
                                   for (s, t), n in sorted(by_pair.items())],
            'retired_types_snapshot': sorted(RETIRED),
            'retired_terminal': estimate(retired_counts, retired_weights),
            'superseded_terminal': estimate(old_counts, old_weights),
            'safe_delete_candidates_30d': {
                **estimate(candidate_counts, candidate_weights),
                'policy': 'retired AND superseded AND success/skipped AND all created/updated/finished < cutoff AND no references/lineage; UTC and existing SQL string-order latest versions protected; active types excluded',
                'review_only': True, 'deletion_authorized': False,
                'reference_scan_complete': not bool(malformed) and archive_dir.is_dir(),
                'cold_archives_provided': archive_dir.is_dir(),
                'known_trace_or_batch_references_in_candidates': 0 if not malformed else None,
                'scope': 'provided_database_and_archives' if archive_dir.is_dir() else 'main_database_only_upper_bound',
                'task_history_rows_lost_if_deleted': sum(candidate_counts.values()),
            },
            'exclusions_first_match': dict(sorted(exclusions.items())),
            'reference_scan': {'columns': coverage, 'archive_dir': str(archive_dir),
                               'archive_dir_exists': archive_dir.exists(), 'cold_archives': archives, 'protected_distinct_tasks': len(protected),
                               'occurrences_by_source_not_additive': dict(sorted(protection_counts.items())),
                               'invalid_json': dict(malformed)},
            'finalize': {'by_persisted_marker': dict(sorted(finalize_counts.items())),
                         'by_upstream_task_type': dict(sorted(finalize_parents.items())),
                         'feishu_backfill_parent_rows': sum(p in feishu_tasks for p, _ in finalize_edges),
                         'origin_limitation': 'pipeline transitions and reconciliation share the same ID/artifacts; their actual enqueue counts cannot be separated from these rows'},
            'storage': {'method': 'dbstat_pages_weighted_by_logical_row_bytes' if page_sizes else 'average_utf8_cell_bytes_plus_32_per_row',
                        'dbstat_error': dbstat_error, 'tasks_table_bytes': table_bytes,
                        'tasks_index_bytes': index_bytes, 'pages_by_object_bytes': page_sizes,
                        'average_logical_row_bytes': round(total_weight / len(rows), 2) if rows else 0,
                        'note': 'subset estimates are not exact reclaimable pages; index estimate uses row fraction; filesystem release is unknown and requires separately approved maintenance'},
            'limitations': [
                'superseded means older created_at and a version different from every newest-time row; hashes are not ordered and this does not establish current durable input version',
                'candidate excludes every active/unknown type, failed/blocked, latest/ambiguous versions, any known graph/reference endpoint, and any row with recent/unknown activity',
                'main DB uses one read snapshot; each cold archive has its own snapshot; concurrent archival requires a fresh preview before any maintenance',
                'missing cold archives make candidate counts an upper bound for the provided DB, not safe deletion counts',
                'all present task-pointer columns/FKs, listed JSON columns and adjacent/explicit cold archives scanned; external logs or deleted historical edges cannot be reconstructed',
                'task manager/search/export history of candidates would still shrink; candidate is a preview, not deletion authorization',
            ],
        }
    finally:
        conn.close()
