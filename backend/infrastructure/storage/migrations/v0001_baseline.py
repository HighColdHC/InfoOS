"""Compatibility baseline for the schema shipped at revision 048eeb05."""

from __future__ import annotations

from collections.abc import Iterable
import logging
from pathlib import Path
import re
import sqlite3


LOGGER = logging.getLogger(__name__)
_MIGRATION_DIR = Path(__file__).parent
_SQL_FILES = (_MIGRATION_DIR / "v0001_core.sql", _MIGRATION_DIR / "v0001_collector.sql")
_LEADING_COMMENTS = re.compile(r"\A(?:\s|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.DOTALL)
_ALTER_ADD_COLUMN = re.compile(
    r"\Aalter\s+table\s+(?:\"([^\"]+)\"|`([^`]+)`|\[([^]]+)\]|([^\s]+))\s+"
    r"add\s+column\s+(?:\"([^\"]+)\"|`([^`]+)`|\[([^]]+)\]|([^\s]+))",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_OBJECT = re.compile(
    r"\Acreate\s+(?:unique\s+)?(?:virtual\s+)?(table|index)\s+(?:if\s+not\s+exists\s+)?"
    r"(?:\"([^\"]+)\"|`([^`]+)`|\[([^]]+)\]|([^\s(]+))",
    re.IGNORECASE | re.DOTALL,
)


class SchemaDriftError(RuntimeError):
    """The existing database has a correctness-relevant schema mismatch."""


def iter_complete_statements(sql_text: str) -> Iterable[str]:
    """Yield complete SQLite statements without splitting on semicolons."""
    pending: list[str] = []
    for line in sql_text.splitlines(keepends=True):
        pending.append(line)
        candidate = "".join(pending).strip()
        if candidate and sqlite3.complete_statement(candidate):
            yield candidate
            pending.clear()
    remainder = "".join(pending).strip()
    if remainder:
        if not sqlite3.complete_statement(remainder):
            raise SchemaDriftError("frozen v0001 SQL ends with an incomplete statement")
        yield remainder


def _without_leading_comments(statement: str) -> str:
    return _LEADING_COMMENTS.sub("", statement).strip()


def _statements() -> list[str]:
    statements: list[str] = []
    for sql_file in _SQL_FILES:
        statements.extend(iter_complete_statements(sql_file.read_text(encoding="utf-8")))
    return statements


def _object_name(statement: str) -> tuple[str, str] | None:
    match = _CREATE_OBJECT.match(_without_leading_comments(statement))
    if match is None:
        return None
    return match.group(1).lower(), next(value for value in match.groups()[1:] if value is not None)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"pragma table_xinfo({_quote_identifier(table)})")}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _apply_statements(conn: sqlite3.Connection, statements: list[str]) -> tuple[set[str], set[str]]:
    table_statements: list[str] = []
    alter_statements: list[str] = []
    index_statements: list[str] = []
    data_statements: list[str] = []
    target_tables: set[str] = set()
    virtual_tables: set[str] = set()

    for statement in statements:
        sql = _without_leading_comments(statement)
        lowered = sql.lower()
        object_info = _object_name(statement)
        if lowered.startswith("pragma "):
            continue
        if lowered.startswith("create table") or lowered.startswith("create virtual table"):
            table_statements.append(statement)
            if object_info is not None:
                target_tables.add(object_info[1])
                if lowered.startswith("create virtual table"):
                    virtual_tables.add(object_info[1])
        elif lowered.startswith("alter table"):
            alter_statements.append(statement)
        elif lowered.startswith("create index") or lowered.startswith("create unique index") or lowered.startswith("drop index"):
            index_statements.append(statement)
        else:
            data_statements.append(statement)

    for statement in table_statements:
        conn.execute(statement)

    for statement in alter_statements:
        match = _ALTER_ADD_COLUMN.match(_without_leading_comments(statement))
        if match is None:
            raise SchemaDriftError(f"unsupported v0001 ALTER statement: {statement[:120]}")
        values = match.groups()
        table = next(value for value in values[:4] if value is not None)
        column = next(value for value in values[4:] if value is not None)
        if table not in target_tables:
            raise SchemaDriftError(f"v0001 ALTER targets unknown table {table!r}")
        if column not in _column_names(conn, table):
            conn.execute(statement)

    for statement in index_statements:
        conn.execute(statement)
    for statement in data_statements:
        conn.execute(statement)
    return target_tables, virtual_tables


def _normalize_type(value: object) -> str:
    return " ".join(str(value or "").upper().split())


def _normalize_default(value: object) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).strip().split())


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, str | None, int, int, int]]:
    return {
        str(row[1]): (
            _normalize_type(row[2]),
            _normalize_default(row[4]),
            int(row[3]),
            int(row[5]),
            int(row[6]),
        )
        for row in conn.execute(f"pragma table_xinfo({_quote_identifier(table)})")
    }


def _foreign_keys(conn: sqlite3.Connection, table: str) -> set[tuple[object, ...]]:
    grouped: dict[int, list[sqlite3.Row | tuple[object, ...]]] = {}
    for row in conn.execute(f"pragma foreign_key_list({_quote_identifier(table)})"):
        grouped.setdefault(int(row[0]), []).append(row)
    result: set[tuple[object, ...]] = set()
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda row: int(row[1]))
        first = ordered[0]
        result.add(
            (
                str(first[2]),
                tuple((str(row[3]), str(row[4])) for row in ordered),
                str(first[5]),
                str(first[6]),
                str(first[7]),
            )
        )
    return result


def _partial_where(sql: object) -> str | None:
    if sql is None:
        return None
    match = re.search(r"\bwhere\b(.*)\Z", str(sql), re.IGNORECASE | re.DOTALL)
    # Keep string-literal case intact; only whitespace and the trailing
    # statement delimiter are presentation differences.
    return " ".join(match.group(1).strip().rstrip(";").split()) if match else None


def _indexes(conn: sqlite3.Connection, table: str) -> dict[str, tuple[tuple[str, ...], bool, str | None]]:
    result: dict[str, tuple[tuple[str, ...], bool, str | None]] = {}
    for row in conn.execute(f"pragma index_list({_quote_identifier(table)})"):
        name = str(row[1])
        keys = tuple(
            str(item[2]) if item[2] is not None else f"<expression:{item[0]}>"
            for item in conn.execute(f"pragma index_xinfo({_quote_identifier(name)})")
            if int(item[5]) == 1
        )
        schema_row = conn.execute(
            "select sql from sqlite_schema where type = 'index' and name = ?", (name,)
        ).fetchone()
        result[name] = (keys, bool(row[2]), _partial_where(schema_row[0] if schema_row else None))
    return result


def _virtual_table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute("select sql from sqlite_schema where type = 'table' and name = ?", (table,)).fetchone()
    return " ".join(str(row[0] if row else "").lower().split())


def _reference_schema(statements: list[str]) -> tuple[sqlite3.Connection, set[str], set[str]]:
    reference = sqlite3.connect(":memory:")
    reference.execute("pragma foreign_keys = on")
    tables, virtual_tables = _apply_statements(reference, statements)
    return reference, tables, virtual_tables


def validate_schema(
    conn: sqlite3.Connection,
    *,
    statements: list[str] | None = None,
    target_tables: set[str] | None = None,
    virtual_tables: set[str] | None = None,
) -> list[str]:
    """Validate semantic schema shape; return non-critical index warnings."""
    source = statements or _statements()
    reference, expected_tables, expected_virtual = _reference_schema(source)
    if target_tables is not None:
        expected_tables = target_tables
    if virtual_tables is not None:
        expected_virtual = virtual_tables
    critical: list[str] = []
    warnings: list[str] = []
    try:
        for table in sorted(expected_tables):
            actual_row = conn.execute(
                "select type from sqlite_schema where type in ('table', 'view') and name = ?", (table,)
            ).fetchone()
            if actual_row is None:
                critical.append(f"missing table {table}")
                continue
            expected_columns = _columns(reference, table)
            actual_columns = _columns(conn, table)
            if expected_columns != actual_columns:
                critical.append(f"column drift in {table}: expected={expected_columns!r} actual={actual_columns!r}")
            expected_fks = _foreign_keys(reference, table)
            actual_fks = _foreign_keys(conn, table)
            if expected_fks != actual_fks:
                critical.append(f"foreign-key drift in {table}: expected={expected_fks!r} actual={actual_fks!r}")
            if table in expected_virtual and _virtual_table_sql(reference, table) != _virtual_table_sql(conn, table):
                critical.append(f"virtual-table drift in {table}")

            expected_indexes = _indexes(reference, table)
            actual_indexes = _indexes(conn, table)
            for name in sorted(set(expected_indexes) | set(actual_indexes)):
                expected = expected_indexes.get(name)
                actual = actual_indexes.get(name)
                if expected == actual:
                    continue
                is_critical = bool((expected and expected[1]) or (actual and actual[1]))
                message = f"index drift for {name} on {table}: expected={expected!r} actual={actual!r}"
                (critical if is_critical else warnings).append(message)
    finally:
        reference.close()
    if critical:
        raise SchemaDriftError("; ".join(critical))
    for warning in warnings:
        LOGGER.warning("non-critical SQLite index drift: %s", warning)
    return warnings


def apply(conn: sqlite3.Connection) -> list[str]:
    """Bring an empty or historical version-zero database to the v0001 baseline."""
    statements = _statements()
    target_tables, virtual_tables = _apply_statements(conn, statements)
    return validate_schema(
        conn,
        statements=statements,
        target_tables=target_tables,
        virtual_tables=virtual_tables,
    )
