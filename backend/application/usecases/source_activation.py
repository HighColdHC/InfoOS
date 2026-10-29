"""Enable or disable scheduling eligibility without submitting collection work."""

from __future__ import annotations

from backend.application.usecases.source_change_actor import SourceChangeActor
from backend.infrastructure.storage.source_change_audit import append_source_change

from pathlib import Path
from typing import Any

from backend.application.usecases.source_configuration_lifecycle import (
    SourceNotFoundError,
    source_lifecycle_result,
)
from backend.infrastructure.storage.repositories import SourceRepository


def set_source_enabled(
    base_dir: Path | str,
    source_id: str,
    enabled: bool,
    *,
    actor: SourceChangeActor = SourceChangeActor(),
) -> dict[str, Any]:
    """Version activation state while preserving all tasks and historical data."""

    repository = SourceRepository(base_dir)
    with repository.connect() as conn:
        conn.execute("begin immediate")
        before = repository.get_maintained_source(source_id, connection=conn)
        if before is None:
            raise SourceNotFoundError(source_id)
        source = repository.set_enabled(source_id, bool(enabled), connection=conn)
        if source is None:
            raise SourceNotFoundError(source_id)
        append_source_change(conn, source_id=source_id, action="enable" if enabled else "disable",
                             before=before, after=source, **actor.as_dict())
    return source_lifecycle_result(source, None)


# Explicit alias for callers that describe the operation as an activation update.
update_source_activation = set_source_enabled
