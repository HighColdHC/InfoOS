"""Bounded historical finalize reads for one Source Run."""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict, cast

from backend.infrastructure.storage.source_run_json import RunArtifacts, parse_run_artifacts

from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.source_run_trace_finalize import load_task_graph_rows
from backend.infrastructure.storage.source_run_trace_queries import (
    GRAPH_DEPTH_LIMIT,
    GRAPH_NODE_LIMIT,
)


class CardProgressTaskRow(TypedDict):
    root_id: str
    task_id: str
    task_type: str
    status: str
    attempt: int
    artifacts: RunArtifacts


class CardProgressGraphRow(CardProgressTaskRow):
    graph_rank: int
    graph_depth: int


class CardProgressGraph(TypedDict):
    graph: list[CardProgressTaskRow]
    page_truncated: bool


def load_card_progress_graph(
    base_dir: Path | str,
    root_ids: list[str],
) -> CardProgressGraph:
    graph: list[CardProgressTaskRow] = []
    truncated = False
    columns = "t.task_id, t.task_type, t.status, t.attempt, t.artifacts_summary"
    with connect_existing(resolve_sqlite_path(base_dir)) as conn:
        for start in range(0, len(root_ids), 400):
            rows = load_task_graph_rows(
                conn,
                root_ids[start:start + 400],
                columns=columns,
                decode=lambda row: cast(CardProgressGraphRow, dict(row) | {
                    "artifacts": parse_run_artifacts(row["artifacts_summary"]),
                }),
                graph_depth_limit=GRAPH_DEPTH_LIMIT,
                graph_node_limit=GRAPH_NODE_LIMIT,
                expansion_limit=None,
            )
            for row in rows:
                rank = row["graph_rank"]
                depth = row["graph_depth"]
                if rank > GRAPH_NODE_LIMIT or depth > GRAPH_DEPTH_LIMIT:
                    truncated = True
                else:
                    graph.append(row)
    return {"graph": graph, "page_truncated": truncated}
