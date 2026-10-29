"""Calibration module: spec §11 Source Capability Matrix 的代码承载。

公开 API:
  - Calibration, CalibrationResult: 校准记录
  - record_calibration: 记录一次校准（无副作用，仅构造对象）
  - CapabilityEntry, compute_capability: 5 档判定
  - can_enter_fetch_queue: 采集队列入口判断
"""
from backend.domain.calibration.matrix import (
    LOGIN_NOT_APPLICABLE,
    LOGIN_VERIFIABLE,
    NO_LOGIN,
    OPTIONAL_LOGIN,
    REQUIRED_LOGIN,
    CapabilityEntry,
    compute_capability,
    can_enter_fetch_queue,
)
from backend.domain.calibration.record import (
    Calibration,
    CalibrationResult,
    record_calibration,
)

__all__ = [
    "Calibration",
    "CalibrationResult",
    "record_calibration",
    "CapabilityEntry",
    "compute_capability",
    "can_enter_fetch_queue",
    "REQUIRED_LOGIN",
    "OPTIONAL_LOGIN",
    "NO_LOGIN",
    "LOGIN_VERIFIABLE",
    "LOGIN_NOT_APPLICABLE",
]
