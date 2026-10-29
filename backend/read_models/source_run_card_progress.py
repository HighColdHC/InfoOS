"""Complete card progress projection across new and reused item pipelines."""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict
from backend.infrastructure.storage.source_run_json import RunCheckpoint
from backend.read_models.source_run_trace_media import CardBuild

from backend.infrastructure.storage.source_run_card_progress_queries import (
    load_card_progress_graph,
)
from backend.read_models.source_run_trace_media import project_card_build


class CompleteCardProgress(TypedDict):
    total: int
    complete: int | None
    partial: int | None
    failed: int | None


def complete_card_progress(
    base_dir: Path | str,
    checkpoint: RunCheckpoint,
    key_roots: list[tuple[tuple[str, str], str | None]],
) -> CompleteCardProgress:
    graph = load_card_progress_graph(
        base_dir,
        [root_id for _, root_id in key_roots if root_id],
    )
    return _progress_fields(project_card_build(checkpoint, graph, []))


def _progress_fields(projected: CardBuild) -> CompleteCardProgress:
    return {"total": projected["total"], "complete": projected["complete"],
            "partial": projected["partial"], "failed": projected["failed"]}
