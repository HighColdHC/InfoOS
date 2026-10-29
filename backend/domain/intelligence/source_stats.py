"""Evidence labels only; source configuration changes require user confirmation."""
from typing import Any, Mapping, TypedDict


class AcquisitionStats(TypedDict):
    fetch_run_count: int
    partial_run_count: int
    partial_run_rate: float | None
    latest_stop_reason: object


LOW_YIELD_THRESHOLD = 0.10
HIGH_YIELD_THRESHOLD = 0.40


def acquisition_stats(row: Mapping[str, Any] | None = None) -> AcquisitionStats:
    evidence = row or {}
    total, partial = evidence.get("fetch_run_count", 0), evidence.get("partial_run_count", 0)
    return {"fetch_run_count": total, "partial_run_count": partial,
            "partial_run_rate": round(partial / total, 3) if total else None,
            "latest_stop_reason": evidence.get("latest_stop_reason")}


def source_verdict(evaluated: int, pass_rate: float | None, min_evaluated: int) -> str:
    if evaluated < min_evaluated:
        return "insufficient_data"
    if pass_rate < LOW_YIELD_THRESHOLD:
        return "low_yield"
    if pass_rate >= HIGH_YIELD_THRESHOLD:
        return "high_yield"
    return "normal"
