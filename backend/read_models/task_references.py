"""Resolve the public task identifiers the source-run trace hands out."""
from __future__ import annotations

import re
from pathlib import Path

from backend.domain.tasks import TaskStatus
from backend.infrastructure.storage.task_queries import TaskReadQuery
from backend.read_models.source_run_trace_safety import task_identifier_alias

_ALIAS_PATTERN = re.compile(r"task_[0-9a-f]{24}")
# The trace only offers retry for these, so they are resolved before the rest.
_RETRY_CANDIDATE_STATUSES = (TaskStatus.FAILED.value, TaskStatus.BLOCKED.value)


def resolve_task_id(base_dir: Path | str, public_id: str) -> str | None:
    """Return the stored task id for a raw id or its opaque trace alias.

    Aliases are one-way digests, so they are matched by streaming task ids
    (never whole rows) over the entire table, retry candidates first. An
    ambiguous alias resolves to nothing.
    """
    query = TaskReadQuery(base_dir)
    if query.get(public_id) is not None:
        return public_id
    if not _ALIAS_PATTERN.fullmatch(public_id):
        return None
    for exclude in (False, True):
        matches = [
            task_id
            for task_id in query.iter_task_ids(statuses=_RETRY_CANDIDATE_STATUSES, exclude=exclude)
            if task_identifier_alias(task_id) == public_id
        ]
        if matches:
            return matches[0] if len(matches) == 1 else None
    return None
