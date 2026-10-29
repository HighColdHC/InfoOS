"""Run-once diagnostics: fetch → durable CARD_BUILD task execution.

Migrated from infoos.cli.run_once (PR 2d of the backend restructure).
The ``infoos.cli.run_once`` shim was removed in PR 6 along with the
rest of the infoos.cli/ directory.
"""
from __future__ import annotations

from pathlib import Path
import warnings

from backend.application.workflows.fetch import _raw_dir
from backend.application.usecases.source_config import load_source_configs
from backend.infrastructure.browser.in_flight import in_flight_section
from backend.application.pipeline.diagnostics import append_error
from backend.application.pipeline.run_result import ErrorEvent, RunResult, SourceSummary
from backend.infrastructure.storage.raw_store import RawItemStore
from backend.infrastructure.storage.repositories import RawItemRepository


def run_once(
    base_dir: Path,
    source_filter: str | None = None,
    source_overrides: dict[str, dict] | None = None,
) -> dict:
    """Run the legacy synchronous diagnostic pipeline.

    This compatibility callable is intentionally not an app-runtime entrypoint:
    HTTP routes and runtime facades submit durable task batches instead.  Keep
    it only for developer diagnostics and the historical CLI contract.
    """
    warnings.warn(
        "run_once is deprecated outside developer diagnostics; submit durable FETCH tasks instead",
        DeprecationWarning,
        stacklevel=2,
    )
    sources = list(load_source_configs(base_dir))
    if source_filter:
        sources = [source for source in sources if source.get("source_id") == source_filter]
    if source_overrides:
        warnings.warn("source_overrides are rejected by stale-safe diagnostics", RuntimeWarning, stacklevel=2)
        sources = [
            {
                **source,
                "_diagnostic_override_rejected": str(source.get("source_id")) in source_overrides,
            }
            for source in sources
        ]
    with in_flight_section(
        "backend.application.workflows.run_once run-once",
        task_count=len(sources),
    ):
        return _run_once_impl(base_dir, sources)


def _run_once_impl(base_dir: Path, sources) -> dict:
    from backend.application.tasks.executor import run_tasks_by_ids
    from backend.application.tasks.types import Task, TaskType
    from backend.application.workflows.acquisition import SourceAdmissionDeniedError, fetch_source
    from backend.application.workflows.fetch_commit import SourceConfigurationChangedError

    run = RunResult.start()
    raw_store = RawItemStore(_raw_dir(base_dir))
    raw_item_repo = RawItemRepository(base_dir)
    raw_output_paths: list[Path] = []
    card_build_task_ids: list[str] = []
    raw_items_persisted = 0
    visible_count = 0
    dedup_skipped_count = 0
    diagnostics_output: Path | None = None

    for source in sources:
        connector_id = source["connector_id"]
        if source.get("_diagnostic_override_rejected"):
            run.sources.append(
                SourceSummary(
                    connector_id=connector_id,
                    source_id=source.get("source_id"),
                    source_lane=source.get("source_lane"),
                    status="skipped",
                )
            )
            continue
        try:
            source_id = str(source["source_id"])
            source_run_id = run.run_id if len(sources) == 1 else f"{run.run_id}_{source_id}"
            task = Task(
                task_id=f"fetch_run_once_{source_id}_{run.run_id.removeprefix('run_')}",
                task_type=TaskType.FETCH,
                target_kind="source",
                target_id=source_id,
                artifacts_summary={
                    "run_id": source_run_id,
                },
            )
            artifacts = fetch_source(base_dir, source_id=source_id, task=task)
            fresh_raw_items = [
                raw_item_repo.get_model(raw_item_id)
                for raw_item_id in artifacts["raw_item_ids"]
            ]
            persisted_items = [raw_item for raw_item in fresh_raw_items if raw_item is not None]
            if persisted_items:
                raw_path = raw_store.append(artifacts["connector_id"], persisted_items)
                raw_output_paths.append(raw_path)
            raw_items_persisted += len(persisted_items)
            visible_count += int(artifacts["visible_count"])
            dedup_skipped_count += int(artifacts["dedup_skipped_count"])
            card_build_task_ids.extend(artifacts["card_build_task_ids"])
            run.sources.append(
                SourceSummary(
                    connector_id=connector_id,
                    source_id=source.get("source_id"),
                    source_lane=source.get("source_lane"),
                    status="success",
                    fetched_count=int(artifacts["fetched_count"]),
                    visible_count=int(artifacts["visible_count"]),
                    new_count=int(artifacts["fetched_count"]),
                    dedup_skipped_count=int(artifacts["dedup_skipped_count"]),
                )
            )
        except (SourceConfigurationChangedError, SourceAdmissionDeniedError):
            run.sources.append(
                SourceSummary(
                    connector_id=connector_id,
                    source_id=source.get("source_id"),
                    source_lane=source.get("source_lane"),
                    status="skipped",
                )
            )
        except Exception as exc:  # noqa: BLE001 - Connector HTTP, browser and subprocess failures must produce per-source error records while other sources continue
            error = ErrorEvent.from_exception(
                run_id=run.run_id,
                stage="connector",
                severity="error",
                connector_id=connector_id,
                message=f"Connector {connector_id} failed: {exc}",
                exc=exc,
            )
            run.errors.append(error)
            diagnostics_output = append_error(base_dir, error)
            run.sources.append(
                SourceSummary(
                    connector_id=connector_id,
                    source_id=source.get("source_id"),
                    source_lane=source.get("source_lane"),
                    status="connector_failed",
                    error_count=1,
                )
            )

    run.outputs.update(
        {
            "raw_outputs": [str(path) for path in raw_output_paths],
            "fetched_count": raw_items_persisted,
            "visible_count": visible_count,
            "dedup_skipped_count": dedup_skipped_count,
            "raw_items_persisted": raw_items_persisted,
            "diagnostic_output": str(diagnostics_output) if diagnostics_output else None,
        }
    )

    if card_build_task_ids:
        build_task_result = run_tasks_by_ids(
            base_dir,
            card_build_task_ids,
            task_types=[TaskType.CARD_BUILD],
        )
        card_ids = [
            str(item["artifacts"].get("card_id"))
            for item in build_task_result["results"]
            if item["status"] == "success" and item["artifacts"].get("card_id")
        ]
        run.cards.generated = len(card_ids)

    run.outputs["built_count"] = run.cards.generated
    run.outputs["database"] = {
        "raw_items_persisted": raw_items_persisted,
        "cards_persisted": run.cards.generated,
    }
    run.outputs["exports"] = {
        "raw_outputs": run.outputs.pop("raw_outputs", []),
        "diagnostic_output": run.outputs.pop("diagnostic_output", None),
    }
    # Clean up any remaining legacy top-level fields
    run.outputs.pop("raw_items_persisted", None)

    run.finish()
    return run.to_dict()


def main(argv: list[str] | None = None) -> int:
    """Module entrypoint: run ``run_once`` and print JSON result.

    Usage:
        python -m backend.application.workflows.run_once --base-dir .
    """
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".")
    args = parser.parse_args(argv)
    from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
    initialize_runtime_storage(Path(args.base_dir))
    result = run_once(base_dir=Path(args.base_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
