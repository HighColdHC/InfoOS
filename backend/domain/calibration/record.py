"""Calibration record: 真实 source 校准记录（spec §11.2）。

校准是 source 从 implemented_uncalibrated 升级到 supported 的唯一通道。
每次校准产出一条 Calibration 记录，由 calibration matrix 视图聚合。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from backend.domain.timestamps import utc_now


class CalibrationResult(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class Calibration:
    """单次真实采集校准记录。"""

    platform: str
    source_type: str
    identifier: str
    result: CalibrationResult
    calibrated_at: datetime
    error_classification: str | None  # 失败时填 FetchFailureReason.value
    notes: str = ""


def record_calibration(
    *,
    platform: str,
    source_type: str,
    identifier: str,
    result: CalibrationResult,
    notes: str = "",
    error_classification: str | None = None,
) -> Calibration:
    """记录一次真实采集校准。

    本函数无副作用——只构造 Calibration 对象。落库由调用方负责。
    实际落库在 Plan A 后续 task 中加 schema migration + 写入路径。
    """
    return Calibration(
        platform=platform,
        source_type=source_type,
        identifier=identifier,
        result=result,
        calibrated_at=utc_now().replace(tzinfo=None),
        error_classification=error_classification,
        notes=notes,
    )
