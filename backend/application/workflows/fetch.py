"""Fetch and persistence helpers: raw items, connectors, database I/O.

Migrated from infoos.cli.ingest (PR 2b of the backend restructure).
The ``infoos.cli.ingest`` shim was removed in PR 6 along with the rest
of the infoos.cli/ directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from backend.application.usecases.source_config import load_source_configs
from backend.application.workflows.acquisition import SourceAdmissionDeniedError, fetch_source, _build_connector as _build_connector
from backend.application.workflows.fetch_commit import SourceConfigurationChangedError
from backend.infrastructure.storage.repositories import RawItemRepository
from backend.infrastructure.storage.raw_store import RawItemStore


def _raw_dir(base_dir: Path) -> Path:
    return base_dir / "data" / "raw"


def fetch(base_dir: Path) -> dict:
    """Compatibility CLI entrypoint that delegates every source to FETCH acquisition."""
    from backend.application.tasks.types import Task, TaskType
    raw_store = RawItemStore(_raw_dir(base_dir))
    raw_item_repo = RawItemRepository(base_dir)
    raw_output_paths: list[Path] = []
    fetched_count = 0
    visible_count = 0
    dedup_skipped_count = 0
    raw_items_persisted = 0
    skipped_sources: list[str] = []

    for source in load_source_configs(base_dir):
        if not source["enabled"]:
            continue
        source_id = str(source["source_id"])
        run_id = f"fetch_cli_{uuid4().hex[:12]}"
        task = Task(
            task_id=f"fetch_cli_{source_id}_{uuid4().hex[:12]}",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id=source_id,
            artifacts_summary={"run_id": run_id},
        )
        try:
            artifacts = fetch_source(base_dir, source_id=source_id, task=task)
        except (SourceConfigurationChangedError, SourceAdmissionDeniedError):
            skipped_sources.append(source_id)
            continue
        raw_items = [raw_item_repo.get_model(raw_item_id) for raw_item_id in artifacts["raw_item_ids"]]
        persisted_items = [raw_item for raw_item in raw_items if raw_item is not None]
        if persisted_items:
            raw_output_paths.append(raw_store.append(artifacts["connector_id"], persisted_items))
        fetched_count += int(artifacts["fetched_count"])
        visible_count += int(artifacts["visible_count"])
        dedup_skipped_count += int(artifacts["dedup_skipped_count"])
        raw_items_persisted += len(persisted_items)

    raw_outputs = [str(path) for path in raw_output_paths]
    return {
        "database": {
            "raw_items_persisted": raw_items_persisted,
        },
        "exports": {
            "raw_outputs": raw_outputs,
        },
        "fetched_count": fetched_count,
        "visible_count": visible_count,
        "dedup_skipped_count": dedup_skipped_count,
        "skipped_sources": skipped_sources,
    }


def main(argv: list[str] | None = None) -> int:
    """Module entrypoint: run ``fetch`` and print JSON result.

    Usage:
        python -m backend.application.workflows.fetch --base-dir .
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".")
    args = parser.parse_args(argv)
    from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
    initialize_runtime_storage(Path(args.base_dir))
    result = fetch(base_dir=Path(args.base_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
