#!/usr/bin/env python3
"""Write a durable SQLite-backed reliability acceptance report.

The report deliberately delegates all operational reads to
``read_reliability_status`` so its numbers match the API and survive process
restarts.  It never reads the default runtime implicitly: callers must pass
the intended base directory.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.read_models.reliability import read_reliability_status


_BROWSER_PLATFORMS = frozenset({"x", "youtube", "reddit", "xiaohongshu", "douyin", "scys"})
_DEFAULT_LEASE_SECONDS = 300


def _rate_summary(acquisition: dict[str, Any], *, browser: bool) -> dict[str, Any]:
    platforms = sorted(
        platform
        for platform in acquisition
        if (platform in _BROWSER_PLATFORMS) is browser
    )
    runs = sum(int(acquisition[platform].get("runs_24h") or 0) for platform in platforms)
    successes = sum(
        round(float(acquisition[platform]["success_rate_24h"]) * int(acquisition[platform].get("runs_24h") or 0))
        for platform in platforms
        if acquisition[platform].get("success_rate_24h") is not None
    )
    return {
        "platforms": platforms,
        "runs_24h": runs,
        "successes_24h": successes,
        "success_rate": round(successes / runs, 4) if runs else None,
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _rate(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _lease_seconds() -> int:
    try:
        return max(2, int(os.environ.get("INFOOS_TASK_LEASE_SECONDS", _DEFAULT_LEASE_SECONDS)))
    except ValueError:
        return _DEFAULT_LEASE_SECONDS


def build_report(
    status: dict[str, Any],
    *,
    public_min_rate: float,
    browser_min_rate: float,
    required_acquisition_groups: tuple[str, ...] = ("public", "browser"),
    max_expired_leases: int,
    max_running_lease_periods: int,
) -> dict[str, Any]:
    """Apply CLI acceptance thresholds to the canonical reliability payload."""
    acquisition = status.get("acquisition") or {}
    rates = {
        "public": _rate_summary(acquisition, browser=False),
        "browser": _rate_summary(acquisition, browser=True),
    }
    violations: list[dict[str, Any]] = []
    expired = int((status.get("leases") or {}).get("expired") or 0)
    if expired > max_expired_leases:
        violations.append(
            {"metric": "expired_leases", "actual": expired, "maximum": max_expired_leases}
        )
    minimum_by_group = {"public": public_min_rate, "browser": browser_min_rate}
    for group in required_acquisition_groups:
        minimum = minimum_by_group[group]
        rate = rates[group]
        if rate["runs_24h"] == 0:
            violations.append(
                {
                    "metric": f"{group}_acquisition_readiness",
                    "reason": "no_runs_24h",
                    "actual": 0,
                    "minimum": 1,
                }
            )
            continue
        actual = rate["success_rate"]
        if actual is not None and actual < minimum:
            violations.append(
                {"metric": f"{group}_success_rate_24h", "actual": actual, "minimum": minimum}
            )
    oldest_running = (status.get("queue") or {}).get("oldest_running_seconds")
    maximum_running_seconds = max_running_lease_periods * _lease_seconds()
    if oldest_running is not None and float(oldest_running) > maximum_running_seconds:
        violations.append(
            {
                "metric": "oldest_running_lease_periods",
                "actual_seconds": float(oldest_running),
                "maximum_seconds": maximum_running_seconds,
                "lease_seconds": _lease_seconds(),
            }
        )
    thresholds = {
        "public_min_rate": public_min_rate,
        "browser_min_rate": browser_min_rate,
        "required_acquisition_groups": list(required_acquisition_groups),
        "max_expired_leases": max_expired_leases,
        "max_running_lease_periods": max_running_lease_periods,
        "lease_seconds": _lease_seconds(),
    }
    return {
        "status": "passed" if not violations else "blocked",
        "generated_at": status.get("generated_at"),
        "thresholds": thresholds,
        "rates": rates,
        "violations": violations,
        "reliability": status,
    }


def _markdown(report: dict[str, Any]) -> str:
    rates = report["rates"]
    lines = [
        "# InfoOS Reliability Acceptance Report",
        "",
        f"- Status: **{report['status']}**",
        f"- Generated at: {report['generated_at']}",
        "",
        "## 24-hour acquisition rates",
        "",
        "| Group | Platforms | Runs | Successes | Rate |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for group in ("public", "browser"):
        rate = rates[group]
        percentage = "n/a" if rate["success_rate"] is None else f"{rate['success_rate']:.2%}"
        lines.append(
            f"| {group} | {', '.join(rate['platforms']) or 'none'} | {rate['runs_24h']} | "
            f"{rate['successes_24h']} | {percentage} |"
        )
    lines.extend(["", "## Threshold violations", ""])
    if report["violations"]:
        for violation in report["violations"]:
            lines.append(f"- `{violation['metric']}`: {json.dumps(violation, ensure_ascii=False, sort_keys=True)}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a SQLite-backed InfoOS reliability acceptance report.")
    parser.add_argument("--base-dir", type=Path, required=True, help="Isolated InfoOS state root containing data/infoos.sqlite3")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--public-min-rate", type=_rate, default=0.98)
    parser.add_argument("--browser-min-rate", type=_rate, default=0.90)
    parser.add_argument(
        "--required-acquisition-group",
        action="append",
        choices=("public", "browser"),
        dest="required_acquisition_groups",
        help="Acquisition group that must have runs and meet its threshold; repeat as needed. Defaults to both.",
    )
    parser.add_argument("--max-expired-leases", type=_nonnegative_int, default=0)
    parser.add_argument("--max-running-lease-periods", type=_nonnegative_int, default=2)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        from backend.infrastructure.storage.database import default_database_path, ensure_database_initialized
        ensure_database_initialized(default_database_path(args.base_dir))
        status = read_reliability_status(args.base_dir)
        report = build_report(
            status,
            public_min_rate=args.public_min_rate,
            browser_min_rate=args.browser_min_rate,
            required_acquisition_groups=tuple(
                getattr(args, "required_acquisition_groups", None) or ("public", "browser")
            ),
            max_expired_leases=args.max_expired_leases,
            max_running_lease_periods=args.max_running_lease_periods,
        )
        _write(args.output_json, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _write(args.output_markdown, _markdown(report))
    except (OSError, ValueError) as exc:
        print(f"infoos reliability report configuration error: {exc}", file=sys.stderr)
        return 2
    print(f"infoos_reliability_report={report['status']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
