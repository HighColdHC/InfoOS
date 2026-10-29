#!/usr/bin/env python3
"""Run one real scheduler -> worker -> SQLite/card acceptance cycle.

This command is for an isolated Docker Appliance acceptance volume. It marks
the canonical 10 x 5 source set due, invokes the production scheduler once,
then observes the already-running production worker. It never starts a second
worker and never uses the manual source-run API.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.domain.sources.registry import configurable_platforms
from backend.infrastructure.security.redaction import redact_secrets, redact_text
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.workers.scheduler.cycle import run_scheduler_cycle

TERMINAL = {
    TaskStatus.SUCCESS.value,
    TaskStatus.FAILED.value,
    TaskStatus.BLOCKED.value,
    TaskStatus.SKIPPED.value,
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def canonical_source_set(
    base_dir: Path,
    *,
    minimum_per_platform: int = 5,
) -> dict[str, list[str]]:
    expected = {item.platform for item in configurable_platforms()}
    sources: dict[str, list[str]] = {platform: [] for platform in expected}
    for source in SourceRepository(base_dir).list_sources(include_disabled=False):
        platform = str(source.get("platform") or "").strip().lower()
        if (
            platform in sources
            and source.get("connector_status") == "supported"
            and source.get("enabled", True)
        ):
            sources[platform].append(str(source["source_id"]))
    if set(sources) != expected:
        raise ValueError("configured platform set does not match the registry")
    short = {
        platform: len(set(source_ids))
        for platform, source_ids in sources.items()
        if len(set(source_ids)) < minimum_per_platform
    }
    if short:
        raise ValueError(f"platforms below source minimum: {short}")
    return {
        platform: sorted(set(source_ids))[:minimum_per_platform]
        for platform, source_ids in sorted(sources.items())
    }


def _ensure_no_active_fetches(base_dir: Path, source_ids: set[str]) -> None:
    active = [
        task.task_id
        for task in TaskRepository.for_base_dir(base_dir).list_tasks(
            task_type=TaskType.FETCH,
            limit=100_000,
        )
        if task.target_id in source_ids
        and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
    ]
    if active:
        raise RuntimeError(
            f"selected sources still have active FETCH tasks: {len(active)}"
        )


def _mark_due(base_dir: Path, source_ids: set[str], now: str) -> None:
    db_path = resolve_sqlite_path(base_dir)
    with sqlite3.connect(db_path) as conn:
        placeholders = ", ".join("?" for _ in source_ids)
        cursor = conn.execute(
            f"""
            update sources set next_run_at = ?, updated_at = ?
            where enabled = 1 and source_id in ({placeholders})
            """,
            (now, now, *sorted(source_ids)),
        )
    if cursor.rowcount != len(source_ids):
        raise RuntimeError(
            f"could not mark every selected source due: {cursor.rowcount}/{len(source_ids)}"
        )


def _task_rows(base_dir: Path, task_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not task_ids:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    db_path = resolve_sqlite_path(base_dir)
    placeholders = ", ".join("?" for _ in task_ids)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        task_rows = conn.execute(
            f"""
            select t.task_id, t.task_type, t.target_id, t.status, t.attempt,
                   t.error_reason, t.artifacts_summary, t.created_at,
                   t.started_at, t.finished_at,
                   count(e.event_id) as claimed_event_count
            from tasks t
            left join task_events e
              on e.task_id = t.task_id and e.event_type = 'claimed'
            where t.task_id in ({placeholders})
            group by t.task_id
            """,
            tuple(task_ids),
        ).fetchall()
    for task in task_rows:
        rows[str(task["task_id"])] = {
            "task_id": str(task["task_id"]),
            "task_type": str(task["task_type"]),
            "source_id": str(task["target_id"]),
            "status": str(task["status"]),
            "attempt": int(task["attempt"]),
            "error_reason": redact_text(str(task["error_reason"] or "")) or None,
            "artifacts": json.loads(task["artifacts_summary"] or "{}"),
            "created_at": task["created_at"],
            "started_at": task["started_at"],
            "finished_at": task["finished_at"],
            "claimed_event_count": int(task["claimed_event_count"]),
        }
    return rows


def _wait_terminal(
    base_dir: Path,
    task_ids: list[str],
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        rows = _task_rows(base_dir, task_ids)
        if len(rows) == len(task_ids) and all(
            row["status"] in TERMINAL for row in rows.values()
        ):
            return rows
        time.sleep(max(0.2, poll_seconds))
    return _task_rows(base_dir, task_ids)


def _chain_evidence(
    base_dir: Path,
    chain_id: str,
    *,
    source_id: str,
    scheduler_started_at: str,
) -> dict[str, Any]:
    db_path = resolve_sqlite_path(base_dir)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tasks = conn.execute(
            """
            select t.task_id, t.task_type, t.status, t.artifacts_summary,
                   t.created_at, t.started_at, t.finished_at,
                   count(e.event_id) as claimed_event_count
            from tasks t
            left join task_events e
              on e.task_id = t.task_id and e.event_type = 'claimed'
            where json_extract(t.artifacts_summary, '$.chain_id') = ?
            group by t.task_id
            """,
            (chain_id,),
        ).fetchall()
        artifacts = [json.loads(row["artifacts_summary"] or "{}") for row in tasks]
        raw_ids = {
            str(raw_id)
            for item in artifacts
            for raw_id in item.get("raw_item_ids") or []
        }
        card_ids = {
            str(item["card_id"]) for item in artifacts if item.get("card_id")
        }
        raw_item_count = 0
        active_card_count = 0
        invalid_cards: list[str] = []
        block_count = 0
        asset_count = 0
        if raw_ids:
            placeholders = ", ".join("?" for _ in raw_ids)
            raw_item_count = int(
                conn.execute(
                    f"""
                    select count(*) from raw_items
                    where source_id = ? and raw_item_id in ({placeholders})
                    """,
                    (source_id, *sorted(raw_ids)),
                ).fetchone()[0]
            )
        if card_ids:
            placeholders = ", ".join("?" for _ in card_ids)
            card_rows = conn.execute(
                f"""
                select card_id, status, version, content_hash
                from cards where source_id = ? and card_id in ({placeholders})
                """,
                (source_id, *sorted(card_ids)),
            ).fetchall()
            active_card_count = sum(
                1
                for row in card_rows
                if row["status"] == "active"
                and int(row["version"] or 0) >= 1
                and bool(row["content_hash"])
            )
            invalid_cards = sorted(
                str(row["card_id"])
                for row in card_rows
                if row["status"] != "active"
                or int(row["version"] or 0) < 1
                or not row["content_hash"]
            )
            block_count = int(
                conn.execute(
                    f"select count(*) from card_content_blocks where card_id in ({placeholders})",
                    tuple(sorted(card_ids)),
                ).fetchone()[0]
            )
            asset_count = int(
                conn.execute(
                    f"select count(*) from card_assets where card_id in ({placeholders})",
                    tuple(sorted(card_ids)),
                ).fetchone()[0]
            )
    task_type_statuses: dict[str, dict[str, int]] = {}
    for row in tasks:
        task_type = str(row["task_type"])
        status = str(row["status"])
        task_type_statuses.setdefault(task_type, {})
        task_type_statuses[task_type][status] = (
            task_type_statuses[task_type].get(status, 0) + 1
        )
    lane_statuses = {
        "core": dict(
            sorted(
                (task_type, statuses)
                for task_type, statuses in task_type_statuses.items()
                if task_type
                not in {
                    TaskType.CARD_LLM_ENRICH.value,
                    TaskType.VIDEO_TRANSCRIBE.value,
                    TaskType.VIDEO_EXTRACT_AUDIO.value,
                }
            )
        ),
        "llm": {
            task_type: statuses
            for task_type, statuses in task_type_statuses.items()
            if task_type == TaskType.CARD_LLM_ENRICH.value
        },
        "asr": {
            task_type: statuses
            for task_type, statuses in task_type_statuses.items()
            if task_type
            in {
                TaskType.VIDEO_TRANSCRIBE.value,
                TaskType.VIDEO_EXTRACT_AUDIO.value,
            }
        },
    }
    return {
        "task_statuses": dict(Counter(str(row["status"]) for row in tasks)),
        "task_type_statuses": task_type_statuses,
        "lane_statuses": lane_statuses,
        "task_count": len(tasks),
        "fresh_task_count": sum(
            1 for row in tasks if str(row["created_at"] or "") >= scheduler_started_at
        ),
        "claimed_task_count": sum(
            1
            for row in tasks
            if int(row["claimed_event_count"] or 0) > 0 and row["started_at"]
        ),
        "raw_item_artifact_count": len(raw_ids),
        "raw_item_count": raw_item_count,
        "card_artifact_count": len(card_ids),
        "card_count": active_card_count,
        "invalid_card_ids": invalid_cards,
        "content_block_count": block_count,
        "asset_count": asset_count,
    }


def _wait_chains_terminal(
    base_dir: Path,
    chains: dict[str, str],
    *,
    scheduler_started_at: str,
    timeout_seconds: int,
    poll_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        evidence = [
            _chain_evidence(
                base_dir,
                chain_id,
                source_id=source_id,
                scheduler_started_at=scheduler_started_at,
            )
            for chain_id, source_id in chains.items()
        ]
        if evidence and all(
            item["task_statuses"]
            and not any(
                status not in TERMINAL for status in item["task_statuses"]
            )
            for item in evidence
        ):
            return
        time.sleep(max(0.2, poll_seconds))
    raise RuntimeError("scheduled task chains did not reach terminal state")


def _existing_task_ids(base_dir: Path, source_ids: set[str]) -> set[str]:
    if not source_ids:
        return set()
    db_path = resolve_sqlite_path(base_dir)
    placeholders = ", ".join("?" for _ in source_ids)
    with sqlite3.connect(db_path) as conn:
        return {
            str(row[0])
            for row in conn.execute(
                f"select task_id from tasks where target_id in ({placeholders})",
                tuple(sorted(source_ids)),
            ).fetchall()
        }


def _completion_failures(result: dict[str, Any]) -> list[str]:
    evidence = result["evidence"]
    failures: list[str] = []
    if result["fetch_status"] != TaskStatus.SUCCESS.value:
        failures.append("fetch_not_success")
    if any(
        statuses.get(TaskStatus.FAILED.value, 0)
        or statuses.get(TaskStatus.BLOCKED.value, 0)
        for statuses in evidence["task_type_statuses"].values()
    ):
        failures.append("chain_failed_or_blocked")
    if evidence["task_count"] < 1:
        failures.append("chain_missing")
    if evidence["fresh_task_count"] != evidence["task_count"]:
        failures.append("historical_chain_task")
    if evidence["claimed_task_count"] != evidence["task_count"]:
        failures.append("worker_claim_missing")
    if evidence["raw_item_count"] < 1:
        failures.append("raw_item_missing")
    if evidence["card_count"] < 1:
        failures.append("active_card_missing")
    if evidence["content_block_count"] < 1:
        failures.append("content_block_missing")
    return failures


def _validate_scheduler_plan(
    planned_ids: list[str],
    planned: dict[str, dict[str, Any]],
    *,
    selected_ids: set[str],
    existing_task_ids: set[str],
) -> dict[str, dict[str, Any]]:
    extra_planned_sources = sorted(
        {
            row["source_id"]
            for row in planned.values()
            if row["source_id"] not in selected_ids
        }
    )
    if (
        len(planned_ids) != len(selected_ids)
        or len(set(planned_ids)) != len(selected_ids)
        or len(planned) != len(selected_ids)
        or extra_planned_sources
    ):
        raise RuntimeError(
            "scheduler plan is not the isolated canonical 10x5 set: "
            f"planned={len(planned_ids)}, loaded={len(planned)}, "
            f"extra_sources={len(extra_planned_sources)}"
        )
    selected_rows = [
        row
        for row in planned.values()
        if row["source_id"] in selected_ids
        and not row["artifacts"].get("manual_run")
    ]
    selected_tasks = {row["source_id"]: row for row in selected_rows}
    missing = sorted(selected_ids - set(selected_tasks))
    duplicate_count = len(selected_rows) - len(selected_tasks)
    reused = sorted(
        row["task_id"] for row in selected_rows if row["task_id"] in existing_task_ids
    )
    if missing or duplicate_count or reused or len(selected_rows) != len(selected_ids):
        raise RuntimeError(
            "scheduler did not create exactly one fresh task per selected source: "
            f"missing={len(missing)}, duplicates={duplicate_count}, reused={len(reused)}"
        )
    return selected_tasks


def _validated_chains(
    terminal: dict[str, dict[str, Any]],
    *,
    expected_count: int,
) -> dict[str, str]:
    if any(
        not row["started_at"] or row["claimed_event_count"] < 1
        for row in terminal.values()
    ):
        raise RuntimeError("scheduled FETCH task is missing worker claim evidence")
    chains = {
        str(row["artifacts"].get("chain_id") or ""): str(row["source_id"])
        for row in terminal.values()
    }
    if "" in chains:
        raise RuntimeError("scheduler-created FETCH task is missing chain_id")
    if len(chains) != expected_count:
        raise RuntimeError("scheduler-created FETCH tasks do not have unique chain_id values")
    return chains


def run_acceptance(
    base_dir: Path,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> dict[str, Any]:
    platform_sources = canonical_source_set(base_dir)
    selected_ids = {
        source_id for source_ids in platform_sources.values() for source_id in source_ids
    }
    _ensure_no_active_fetches(base_dir, selected_ids)
    started_at = _now()
    existing_task_ids = _existing_task_ids(base_dir, selected_ids)
    _mark_due(base_dir, selected_ids, started_at)
    scheduler = run_scheduler_cycle(base_dir=base_dir)
    planned_ids = list(scheduler.get("sources", {}).get("planned_task_ids") or [])
    planned = _task_rows(base_dir, planned_ids)
    selected_tasks = _validate_scheduler_plan(
        planned_ids,
        planned,
        selected_ids=selected_ids,
        existing_task_ids=existing_task_ids,
    )
    terminal = _wait_terminal(
        base_dir,
        [row["task_id"] for row in selected_tasks.values()],
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    if any(row["status"] not in TERMINAL for row in terminal.values()):
        raise RuntimeError("scheduled FETCH tasks did not reach terminal state")
    chains = _validated_chains(terminal, expected_count=len(selected_ids))
    _wait_chains_terminal(
        base_dir,
        chains,
        scheduler_started_at=started_at,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    per_platform: dict[str, Any] = {}
    for platform, source_ids in platform_sources.items():
        source_results = []
        for source_id in source_ids:
            task = terminal[selected_tasks[source_id]["task_id"]]
            chain_id = str(task["artifacts"].get("chain_id") or "")
            source_results.append(
                {
                    "source_id": source_id,
                    "fetch_task_id": task["task_id"],
                    "fetch_status": task["status"],
                    "attempt": task["attempt"],
                    "error_reason": task["error_reason"],
                    "chain_id": chain_id,
                    "evidence": _chain_evidence(
                        base_dir,
                        chain_id,
                        source_id=source_id,
                        scheduler_started_at=started_at,
                    ),
                }
            )
        per_platform[platform] = {
            "source_count": len(source_results),
            "sources": source_results,
        }
    incomplete = [
        {
            "source": f"{platform}:{item['source_id']}",
            "reasons": _completion_failures(item),
        }
        for platform, result in per_platform.items()
        for item in result["sources"]
        if _completion_failures(item)
    ]
    circuits = RuntimeCircuitRepository(base_dir).list_safe()
    db_path = resolve_sqlite_path(base_dir)
    with sqlite3.connect(db_path) as conn:
        expired_resources = int(
            conn.execute(
                "select count(*) from runtime_resource_leases where lease_expires_at <= ?",
                (_now(),),
            ).fetchone()[0]
        )
    return redact_secrets({
        "status": "passed" if not incomplete and not expired_resources else "blocked",
        "generated_at": _now(),
        "scheduler_started_at": started_at,
        "scheduler": scheduler,
        "expected_platforms": sorted(platform_sources),
        "per_platform": per_platform,
        "incomplete_sources": incomplete,
        "runtime_circuits": circuits,
        "expired_resource_leases": expired_resources,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_acceptance(
            args.base_dir,
            timeout_seconds=max(1, args.timeout_seconds),
            poll_seconds=max(0.2, args.poll_seconds),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 - Acceptance process boundary must report a redacted error and distinct exit code for any failed stage
        print(f"scheduler acceptance error: {redact_text(str(exc))}", file=sys.stderr)
        return 2
    print(f"infoos_scheduler_acceptance={report['status']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
