"""Delete a maintained source without touching history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.application.usecases.source_configuration_lifecycle import SourceNotFoundError
from backend.infrastructure.storage.repositories import SourceRepository


class SourceHasHistoryError(RuntimeError):
    """The source still owns persisted history and can only be disabled."""


def delete_source_configuration(base_dir: Path | str, source_id: str) -> None:
    repository = SourceRepository(base_dir)
    if repository.get_maintained_source(source_id) is None:
        raise SourceNotFoundError(source_id)
    try:
        deleted = repository.delete_source(source_id)
    except sqlite3.IntegrityError as exc:
        raise SourceHasHistoryError(source_id) from exc
    if not deleted:
        raise SourceNotFoundError(source_id)
