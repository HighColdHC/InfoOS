"""Process-local public Fetch aliases, incrementally read from each database."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from backend.infrastructure.storage.source_run_trace_queries import SourceRunTraceQueries

from backend.read_models.source_run_trace_safety import task_identifier_alias


@dataclass
class _AliasIndex:
    last_rowid: int = 0
    aliases: dict[str, str | None] = field(default_factory=dict)


_INDEXES: dict[Path, _AliasIndex] = {}
_LOCK = Lock()


def resolve_source_run_alias(queries: SourceRunTraceQueries, source_run_id: str) -> str | None:
    if not re.fullmatch(r"task_[0-9a-f]{24}", source_run_id):
        raise ValueError("invalid source run id")
    with _LOCK:
        index = _INDEXES.setdefault(queries.db_path.resolve(), _AliasIndex())
        # Refresh hits too: a newly inserted Fetch may collide with a cached alias.
        last_rowid, tasks = queries.fetch_task_ids_since(index.last_rowid)
        for _, task_id in tasks:
            alias = task_identifier_alias(task_id)
            if alias in index.aliases and index.aliases[alias] != task_id:
                index.aliases[alias] = None
            else:
                index.aliases[alias] = task_id
        index.last_rowid = last_rowid
        return index.aliases.get(source_run_id)
