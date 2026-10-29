#!/usr/bin/env python3
"""Run the durable pipeline repeatedly against authorized live test sources.

This command deliberately drives the same ``/api/source-run-batches`` boundary
as the workbench.  It never invokes a connector directly.  A short-lived task
worker is started for the explicitly selected isolated runtime so every planned
chain is consumed by the production task pipeline before it is audited.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application.tasks.chain_outputs import acquired_chain_output_evidence
from backend.domain.sources.registry import configurable_platforms
from backend.infrastructure.security.redaction import redact_text


_CLASSIFICATIONS = frozenset(
    {
        "success",
        "connector_failure",
        "blocked_auth",
        "blocked_config",
        "timeout",
        "pipeline_failure",
    }
)
_TERMINAL_BATCH_STATUSES = frozenset({"success", "partial", "failed", "skipped"})
_TERMINAL_TASK_STATUSES = frozenset({"success", "failed", "blocked", "skipped"})


def validate_config(
    *,
    base_dir: Path,
    public_source_ids: list[str],
    browser_source_ids: list[str],
    runs_per_source: int,
) -> None:
    if not public_source_ids or not browser_source_ids:
        raise ValueError("at least one public and one browser source id are required")
    total_runs = (len(public_source_ids) + len(browser_source_ids)) * runs_per_source
    if total_runs < 20:
        raise ValueError(f"planned total runs must be at least 20 (got {total_runs})")
    if not base_dir.is_absolute():
        raise ValueError("--base-dir must be an absolute isolated runtime path")


def _rate(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live InfoOS source acceptance through task batches.")
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--public-source-id", action="append", default=[])
    parser.add_argument("--browser-source-id", action="append", default=[])
    parser.add_argument("--runs-per-source", type=_positive_int, required=True)
    parser.add_argument("--timeout-seconds", type=_positive_int, default=180)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-min-rate", type=_rate, default=0.98)
    parser.add_argument("--browser-min-rate", type=_rate, default=0.90)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument(
        "--stop-on-first-blocker",
        action="store_true",
        help="write a partial blocked report after the first non-success chain instead of repeating a known failure",
    )
    return parser.parse_args(argv)


def build_report(
    *,
    attempts: list[dict[str, Any]],
    public_min_rate: float,
    browser_min_rate: float,
    expired_leases: int,
    oldest_running_seconds: float | None,
    lease_seconds: int,
    planned_runs: int | None = None,
    expected_platform_count: int = 10,
    minimum_sources_per_platform: int = 5,
) -> dict[str, Any]:
    """Classify attempts without silently removing blocked source readiness."""
    groups: dict[str, dict[str, Any]] = {}
    platforms: dict[str, dict[str, Any]] = {}
    violations: list[dict[str, Any]] = []
    for group, minimum in (("public", public_min_rate), ("browser", browser_min_rate)):
        selected = [item for item in attempts if item.get("group") == group]
        counts = Counter(str(item.get("classification")) for item in selected)
        connector_attempts = sum(
            counts[classification]
            for classification in ("success", "connector_failure", "timeout", "pipeline_failure")
        )
        successes = counts["success"]
        success_rate = round(successes / connector_attempts, 4) if connector_attempts else None
        groups[group] = {
            "planned_runs": len(selected),
            "counts": dict(sorted(counts.items())),
            "connector_attempts": connector_attempts,
            "connector_successes": successes,
            "success_rate": success_rate,
        }
        for blocked in ("blocked_auth", "blocked_config"):
            if counts[blocked]:
                violations.append(
                    {"metric": f"{group}_{blocked}", "actual": counts[blocked], "maximum": 0}
                )
        if connector_attempts == 0:
            violations.append({"metric": f"{group}_readiness_runs", "actual": 0, "minimum": 1})
        elif success_rate is not None and success_rate < minimum:
            violations.append({"metric": f"{group}_success_rate", "actual": success_rate, "minimum": minimum})

    platform_names = sorted({
        str(item["platform"])
        for item in attempts
        if str(item.get("platform") or "").strip()
    })
    canonical_platform_names = sorted(
        definition.platform for definition in configurable_platforms()
    )
    for platform in platform_names:
        selected = [item for item in attempts if str(item.get("platform")) == platform]
        counts = Counter(str(item.get("classification")) for item in selected)
        connector_attempts = sum(
            counts[classification]
            for classification in ("success", "connector_failure", "timeout", "pipeline_failure")
        )
        successes = counts["success"]
        success_rate = round(successes / connector_attempts, 4) if connector_attempts else None
        group = "browser" if any(item.get("group") == "browser" for item in selected) else "public"
        minimum = browser_min_rate if group == "browser" else public_min_rate
        blocked_count = counts["blocked_auth"] + counts["blocked_config"]
        platform_status = (
            "pass"
            if connector_attempts
            and success_rate is not None
            and success_rate >= minimum
            and blocked_count == 0
            else "blocked"
        )
        platforms[platform] = {
            "group": group,
            "source_count": len({str(item.get("source_id") or "") for item in selected}),
            "planned_runs": len(selected),
            "counts": dict(sorted(counts.items())),
            "connector_attempts": connector_attempts,
            "connector_successes": successes,
            "success_rate": success_rate,
            "minimum": minimum,
            "status": platform_status,
        }
        if platform_status != "pass":
            violations.append(
                {
                    "metric": f"platform_{platform}_readiness",
                    "actual": success_rate,
                    "minimum": minimum,
                    "blocked": blocked_count,
                }
            )
        if platforms[platform]["source_count"] < minimum_sources_per_platform:
            violations.append(
                {
                    "metric": f"platform_{platform}_source_coverage",
                    "actual": platforms[platform]["source_count"],
                    "minimum": minimum_sources_per_platform,
                }
            )

    if platforms and platform_names != canonical_platform_names:
        violations.append(
            {
                "metric": "platform_registry_set",
                "actual": platform_names,
                "expected": canonical_platform_names,
            }
        )
    if platforms and len(platforms) != expected_platform_count:
        violations.append(
            {
                "metric": "platform_count",
                "actual": len(platforms),
                "expected": expected_platform_count,
            }
        )

    pipeline_failures = sum(1 for item in attempts if item.get("classification") == "pipeline_failure")
    if pipeline_failures:
        violations.append({"metric": "pipeline_failures", "actual": pipeline_failures, "maximum": 0})
    if expired_leases:
        violations.append({"metric": "expired_leases", "actual": expired_leases, "maximum": 0})
    max_running_seconds = max(1, lease_seconds) * 2
    if oldest_running_seconds is not None and oldest_running_seconds > max_running_seconds:
        violations.append(
            {
                "metric": "oldest_running_lease_periods",
                "actual_seconds": oldest_running_seconds,
                "maximum_seconds": max_running_seconds,
            }
        )
    expected_runs = int(planned_runs if planned_runs is not None else len(attempts))
    if len(attempts) < expected_runs:
        violations.append(
            {"metric": "completed_runs", "actual": len(attempts), "expected": expected_runs}
        )
    return {
        "status": "passed" if not violations else "blocked",
        "thresholds": {
            "public_min_rate": public_min_rate,
            "browser_min_rate": browser_min_rate,
            "max_expired_leases": 0,
            "max_running_lease_periods": 2,
            "lease_seconds": lease_seconds,
            "expected_platform_count": expected_platform_count,
            "minimum_sources_per_platform": minimum_sources_per_platform,
        },
        "groups": groups,
        "platforms": platforms,
        "classification_names": sorted(_CLASSIFICATIONS),
        "attempts": attempts,
        "planned_runs": expected_runs,
        "executed_runs": len(attempts),
        "expired_leases": expired_leases,
        "oldest_running_seconds": oldest_running_seconds,
        "violations": violations,
    }


def _request_json(url: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"content-type": "application/json"}
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        parsed = urlsplit(url)
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310 - loopback URL supplied by operator
            decoded = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"request to {url} failed: {exc}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"request to {url} returned a non-object response")
    return decoded


def _start_worker(base_dir: Path) -> subprocess.Popen[str]:
    environment = dict(os.environ)
    environment["INFOOS_STATE_HOME"] = str(base_dir)
    environment["INFOOS_DATA_HOME"] = str(base_dir / "data")
    return subprocess.Popen(
        [sys.executable, "-m", "backend.workers.task_worker", "--base-dir", ".", "--poll-seconds", "0.2"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop_worker(worker: subprocess.Popen[str]) -> str:
    worker.terminate()
    try:
        _, stderr = worker.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        worker.kill()
        _, stderr = worker.communicate(timeout=10)
    return stderr or ""


def _db(base_dir: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(base_dir / "data" / "infoos.sqlite3", timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _task_row(base_dir: Path, task_id: str) -> dict[str, Any] | None:
    with _db(base_dir) as conn:
        row = conn.execute("select * from tasks where task_id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["artifacts_summary"] = _load_json(result.get("artifacts_summary"), {})
    return result


def _source_platform(base_dir: Path, source_id: str) -> str | None:
    with _db(base_dir) as conn:
        row = conn.execute(
            "select platform from sources where source_id = ?",
            (source_id,),
        ).fetchone()
    return str(row["platform"]) if row is not None else None


def is_fresh_manual_task(task: dict[str, Any], *, submitted_at: str) -> bool:
    """Reject a historical queue entry silently attached to a new manual batch."""
    created_at = _parse_time(task.get("created_at"))
    submitted = _parse_time(submitted_at)
    return created_at is not None and submitted is not None and created_at >= submitted


def _load_json(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _read_attempt_artifacts(base_dir: Path, task_id: str) -> dict[str, Any]:
    task = _task_row(base_dir, task_id)
    if task is None:
        return {"classification": "pipeline_failure", "reason": "planned fetch task disappeared"}
    artifacts = task["artifacts_summary"]
    chain_id = artifacts.get("chain_id")
    task_status = str(task.get("status") or "")
    events = _events_for_task(base_dir, task_id)
    failure_class = next((event.get("failure_class") for event in reversed(events) if event.get("failure_class")), None)
    result: dict[str, Any] = {
        "task_id": task_id,
        "chain_id": chain_id,
        "task_status": task_status,
        "failure_class": failure_class,
        "task_events": [str(event.get("event_type")) for event in events],
        "raw_item_commit_count": len(artifacts.get("raw_item_ids") or []),
        "card_build_task_count": len(artifacts.get("card_build_task_ids") or []),
    }
    if task_status == "success":
        acquisition_committed = any(event.get("event_type") == "acquisition_committed" for event in events)
        result["acquisition_committed"] = acquisition_committed
        result["output"] = _chain_output(base_dir, str(chain_id or ""))
        if not acquisition_committed:
            result["classification"] = "pipeline_failure"
            result["reason"] = "successful fetch did not persist acquisition_committed event"
        elif _chain_has_pipeline_failure(base_dir, str(chain_id or "")):
            result["classification"] = "pipeline_failure"
            result["reason"] = "downstream task in chain failed or blocked"
        elif (
            result["output"]["missing_output_card_ids"]
            or result["output"]["invalid_output_card_ids"]
            or result["output"]["unresolved_card_build_task_ids"]
        ):
            result["classification"] = "pipeline_failure"
            result["reason"] = "acquired card did not produce a valid output or explicit ignore"
        else:
            result["classification"] = "success"
        return result
    if task_status in {"blocked", "skipped"}:
        result["classification"] = _blocked_classification(failure_class, str(task.get("error_reason") or ""))
        return result
    if task_status == "failed":
        result["classification"] = "connector_failure"
        return result
    result["classification"] = "pipeline_failure"
    result["reason"] = f"fetch task ended with unexpected status {task_status!r}"
    return result


def _events_for_task(base_dir: Path, task_id: str) -> list[dict[str, Any]]:
    with _db(base_dir) as conn:
        rows = conn.execute(
            "select event_type, failure_class, payload, occurred_at from task_events where task_id = ? order by occurred_at, rowid",
            (task_id,),
        ).fetchall()
    return [dict(row) | {"payload": _load_json(row["payload"], {})} for row in rows]


def _blocked_classification(failure_class: Any, error_reason: str) -> str:
    text = f"{failure_class or ''} {error_reason}".lower()
    if any(marker in text for marker in ("auth", "login", "browser_connect", "credential")):
        return "blocked_auth"
    return "blocked_config"


def _chain_has_pipeline_failure(base_dir: Path, chain_id: str) -> bool:
    if not chain_id:
        return True
    with _db(base_dir) as conn:
        rows = conn.execute("select status from tasks where artifacts_summary like ?", (f'%"chain_id": "{chain_id}"%',)).fetchall()
    return any(str(row["status"]) in {"failed", "blocked"} for row in rows)


def _chain_task_statuses(base_dir: Path, chain_id: str) -> list[str]:
    if not chain_id:
        return []
    with _db(base_dir) as conn:
        rows = conn.execute(
            "select status from tasks where artifacts_summary like ? order by created_at, task_id",
            (f'%"chain_id": "{chain_id}"%',),
        ).fetchall()
    return [str(row["status"] or "") for row in rows]


def _chain_output(base_dir: Path, chain_id: str) -> dict[str, Any]:
    return acquired_chain_output_evidence(base_dir, chain_id)


def _reliability_guardrails(base_dir: Path) -> tuple[int, float | None, int]:
    with _db(base_dir) as conn:
        rows = conn.execute("select status, lease_expires_at, started_at from tasks where status = 'running'").fetchall()
    now = datetime.now(timezone.utc)
    expired = 0
    ages: list[float] = []
    for row in rows:
        lease = _parse_time(row["lease_expires_at"])
        started = _parse_time(row["started_at"])
        if lease is None or lease < now:
            expired += 1
        if started is not None:
            ages.append(max(0.0, (now - started).total_seconds()))
    try:
        lease_seconds = max(1, int(os.environ.get("INFOOS_TASK_LEASE_SECONDS", "300")))
    except ValueError:
        lease_seconds = 300
    return expired, max(ages) if ages else None, lease_seconds


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def safe_stderr_tail(value: str, *, limit: int = 4000) -> str:
    return redact_text(str(value or ""))[-max(1, int(limit)) :]


def _wait_for_batch(api_url: str, batch_id: str, *, timeout_seconds: int, poll_seconds: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = _request_json(f"{api_url.rstrip('/')}/api/task-batches/{batch_id}", "GET")
        if not response.get("ok"):
            raise RuntimeError(f"batch {batch_id} could not be read: {response}")
        batch = response.get("batch") or {}
        if str(batch.get("status")) in _TERMINAL_BATCH_STATUSES:
            return batch
        time.sleep(max(0.05, poll_seconds))
    return None


def _wait_for_chain_terminal(
    base_dir: Path,
    chain_id: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> list[str] | None:
    """Wait for all work produced by one manual source run, including retries."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        statuses = _chain_task_statuses(base_dir, chain_id)
        if statuses and all(status in _TERMINAL_TASK_STATUSES for status in statuses):
            return statuses
        time.sleep(max(0.05, poll_seconds))
    return None


def _run_once(
    *,
    api_url: str,
    base_dir: Path,
    source_id: str,
    group: str,
    timeout_seconds: int,
    poll_seconds: float,
) -> dict[str, Any]:
    submitted_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    response = _request_json(
        f"{api_url.rstrip('/')}/api/source-run-batches",
        "POST",
        {"source_ids": [source_id], "limit": 1},
    )
    batch = response.get("batch") or {}
    batch_id = str(batch.get("batch_id") or "")
    task_ids = [str(task_id) for task_id in batch.get("task_ids") or []]
    common = {
        "source_id": source_id,
        "platform": _source_platform(base_dir, source_id),
        "group": group,
        "batch_id": batch_id,
        "fetch_task_ids": task_ids,
    }
    if not batch_id or not task_ids:
        return common | {"classification": "blocked_config", "reason": "source run batch did not plan a fetch task"}
    planned_task = _task_row(base_dir, task_ids[0])
    if planned_task is None or not is_fresh_manual_task(planned_task, submitted_at=submitted_at):
        return common | {
            "classification": "pipeline_failure",
            "reason": "manual source batch reused a historical fetch task",
        }
    completed_batch = _wait_for_batch(
        api_url,
        batch_id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    if completed_batch is None:
        return common | {"classification": "timeout", "reason": "task chain did not reach terminal state"}
    chain_id = str((planned_task.get("artifacts_summary") or {}).get("chain_id") or "")
    if not _wait_for_chain_terminal(
        base_dir,
        chain_id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    ):
        return common | {
            "chain_id": chain_id,
            "classification": "timeout",
            "reason": "task chain did not reach terminal state after batch completion",
        }
    audited = _read_attempt_artifacts(base_dir, task_ids[0])
    return common | {"batch_status": completed_batch.get("status")} | audited


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    base_dir = args.base_dir.expanduser().resolve()
    validate_config(
        base_dir=base_dir,
        public_source_ids=list(args.public_source_id),
        browser_source_ids=list(args.browser_source_id),
        runs_per_source=args.runs_per_source,
    )
    db_path = base_dir / "data" / "infoos.sqlite3"
    if not db_path.exists():
        raise ValueError(f"isolated runtime database does not exist: {db_path}")

    worker = _start_worker(base_dir)
    attempts: list[dict[str, Any]] = []
    worker_stderr = ""
    try:
        stop = False
        for group, source_ids in (("public", args.public_source_id), ("browser", args.browser_source_id)):
            for source_id in source_ids:
                for run_number in range(1, args.runs_per_source + 1):
                    attempt = _run_once(
                        api_url=args.api_url,
                        base_dir=base_dir,
                        source_id=source_id,
                        group=group,
                        timeout_seconds=args.timeout_seconds,
                        poll_seconds=args.poll_seconds,
                    )
                    attempt["run_number"] = run_number
                    attempts.append(attempt)
                    if args.stop_on_first_blocker and attempt.get("classification") != "success":
                        stop = True
                        break
                if stop:
                    break
            if stop:
                break
    finally:
        worker_stderr = _stop_worker(worker)

    expired_leases, oldest_running_seconds, lease_seconds = _reliability_guardrails(base_dir)
    report = build_report(
        attempts=attempts,
        public_min_rate=args.public_min_rate,
        browser_min_rate=args.browser_min_rate,
        expired_leases=expired_leases,
        oldest_running_seconds=oldest_running_seconds,
        lease_seconds=lease_seconds,
        planned_runs=(len(args.public_source_id) + len(args.browser_source_id)) * args.runs_per_source,
    )
    report.update(
        {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "execution_mode": "manual_diagnostic",
            "base_dir": str(base_dir),
            "api_url": args.api_url,
            "source_ids": {"public": list(args.public_source_id), "browser": list(args.browser_source_id)},
            "unique_chain_ids": sorted({str(item["chain_id"]) for item in attempts if item.get("chain_id")}),
            "worker_stderr_tail": safe_stderr_tail(worker_stderr),
        }
    )
    if len(report["unique_chain_ids"]) != len(attempts):
        report["status"] = "blocked"
        report["violations"].append(
            {"metric": "unique_chain_ids", "actual": len(report["unique_chain_ids"]), "expected": len(attempts)}
        )
    return report


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = run_acceptance(args)
        _write_report(args.output, report)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"live source acceptance configuration error: {exc}", file=sys.stderr)
        return 2
    print(f"infoos_live_source_acceptance={report['status']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
