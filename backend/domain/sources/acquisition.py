"""Optional batch evidence, independent of task success and usable item counts."""
from typing import Any, NotRequired, TypedDict


class Acquisition(TypedDict):
    requested_count: int
    observed_count: int
    complete: bool
    stop_reason: str | None
    stop_position: int | None
    stop_message: NotRequired[str]


class AcquisitionArtifacts(TypedDict, total=False):
    acquisition: Acquisition
    completeness_status: str
    requested_count: int
    observed_count: int
    stop_reason: str | None


def normalize_acquisition(value: Any) -> Acquisition | None:
    if not isinstance(value, dict):
        return None
    requested, observed = value.get("requested_count"), value.get("observed_count")
    if (type(requested) is not int or requested < 1
            or type(observed) is not int or observed < 0
            or type(value.get("complete")) is not bool
            or "stop_reason" not in value or "stop_position" not in value):
        return None
    reason, position = value["stop_reason"], value["stop_position"]
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        return None
    if position is not None and (type(position) is not int or position < 1):
        return None
    acquisition: Acquisition = {
        "requested_count": requested, "observed_count": observed,
        "complete": value["complete"] and observed == requested and reason is None,
        "stop_reason": reason[:160] if reason else None, "stop_position": position,
    }
    if isinstance(value.get("stop_message"), str):
        acquisition["stop_message"] = value["stop_message"][:160]
    return acquisition


def acquisition_artifacts(value: Any) -> AcquisitionArtifacts:
    acquisition = normalize_acquisition(value)
    if acquisition is None:
        return {}
    return {"acquisition": acquisition,
            "completeness_status": "complete" if acquisition["complete"] else "partial",
            "requested_count": acquisition["requested_count"],
            "observed_count": acquisition["observed_count"],
            "stop_reason": acquisition["stop_reason"]}
