"""Persisted source support statuses."""
from enum import Enum


class SourceStatus(str, Enum):
    """Source support status; promotion to supported requires calibration."""

    SUPPORTED = "supported"
    IMPLEMENTED_UNCALIBRATED = "implemented_uncalibrated"
    PARTIAL = "partial"
    PLANNED = "planned"
    UNSUPPORTED = "unsupported"
