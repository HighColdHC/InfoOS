"""v0024: freeze raw JSON predicates for backfill and trigger maintenance."""
from pathlib import Path
import sqlite3

from .v0001_baseline import SchemaDriftError, iter_complete_statements
from .v0015_card_read_keys import _trigger_tokens


# Frozen from opportunity_candidates at v23; only the raw-payload portions.
PREFILTER_CANDIDATE = """((json_type(r.raw_payload, '$.prefilter.status') in ('text')
    and json_extract(r.raw_payload, '$.prefilter.status') in ('matched','unknown'))
    or (json_type(r.raw_payload, '$.prefilter') is null
    and json_extract(r.raw_payload, '$.raw_metadata.prefilter.status') in ('matched','unknown')))"""
WIND_VANE_TAGGED = """(json_type(r.raw_payload, '$.raw_metadata.tags')='array' and exists (
            select 1 from json_each(r.raw_payload, '$.raw_metadata.tags') tag
            where tag.type='text' and instr(tag.value, '风向标') > 0))"""


def _statements() -> list[str]:
    template = Path(__file__).with_suffix('.sql').read_text(encoding='utf-8')
    return list(iter_complete_statements(template.format(
        prefilter_new=PREFILTER_CANDIDATE.replace('r.raw_payload', 'new.raw_payload'),
        wind_vane_new=WIND_VANE_TAGGED.replace('r.raw_payload', 'new.raw_payload'),
        prefilter_raw=PREFILTER_CANDIDATE,
        wind_vane_raw=WIND_VANE_TAGGED,
    )))


STATEMENTS = _statements()
SCHEMA_STATEMENTS = STATEMENTS[:-1]
TRIGGERS = dict(zip(('raw_item_candidate_flags_after_insert', 'raw_item_candidate_flags_after_update'),
                    SCHEMA_STATEMENTS[1:]))


def validate(conn: sqlite3.Connection) -> None:
    """Baseline validates columns/PK/FK; also require WITHOUT ROWID and triggers."""
    table = next(row for row in conn.execute('pragma table_list') if row[1] == 'raw_item_candidate_flags')
    if table[4] != 1:
        raise SchemaDriftError('rowid drift for raw_item_candidate_flags')
    for name, expected in TRIGGERS.items():
        row = conn.execute("select sql from sqlite_schema where type='trigger' and name=?", (name,)).fetchone()
        if row is None or _trigger_tokens(row[0]) != _trigger_tokens(expected):
            raise SchemaDriftError(f'trigger drift for {name}')
