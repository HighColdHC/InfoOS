"""Capability matrix: source 支持判定（spec §11.1/11.2）。

判定规则：
  - explicit_unsupported=True → unsupported（无论其他条件）
  - code_supported=False       → planned（spec 有但代码未动）
  - code_supported=True, calibration=SUCCESS → supported
  - code_supported=True, calibration=PARTIAL → partial
  - code_supported=True, calibration=FAILED/None → implemented_uncalibrated
  - code_supported=True, known_gap 非空 → partial（覆盖上面）

can_enter_fetch_queue() 是 UI / scheduler 的入口判断：
  - supported / partial → True
  - implemented_uncalibrated / planned / unsupported → False
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.domain.calibration.record import Calibration, CalibrationResult


# Login 属性常量（spec §11.2 详细属性表）
REQUIRED_LOGIN = "required"          # 必须登录才能采集
OPTIONAL_LOGIN = "optional"          # 可选登录（公开内容也能采）
NO_LOGIN = "no"                      # 不需要登录（如 RSS）
LOGIN_VERIFIABLE = "verifiable"      # login_probe 可验证
LOGIN_NOT_APPLICABLE = "n/a"         # 登录不适用

SourceStatusLiteral = Literal[
    "supported",
    "implemented_uncalibrated",
    "partial",
    "planned",
    "unsupported",
]


@dataclass(frozen=True)
class CapabilityEntry:
    platform: str
    source_type: str
    status: SourceStatusLiteral
    requires_login: str          # REQUIRED_LOGIN / OPTIONAL_LOGIN / NO_LOGIN
    login_verifiable: str        # LOGIN_VERIFIABLE / LOGIN_NOT_APPLICABLE
    default_cadence: str         # "1h" / "30min" / "6h" / "on_demand"
    calibrated_at: str | None    # ISO 8601 timestamp
    calibration_result: str | None  # "success" / "partial" / "failed"
    known_gap: str | None = None


def compute_capability(
    *,
    platform: str,
    source_type: str,
    code_supported: bool,
    calibration: Calibration | None,
    default_cadence: str = "1h",
    requires_login: str = REQUIRED_LOGIN,
    login_verifiable: str = LOGIN_VERIFIABLE,
    known_gap: str | None = None,
    explicit_unsupported: bool = False,
) -> CapabilityEntry:
    """根据 code 支持状态 + 校准记录计算当前 capability。"""
    calibrated_at = calibration.calibrated_at.isoformat() if calibration else None
    calibration_result = calibration.result.value if calibration else None

    # 优先级最高的 unsupported 优先判定
    if explicit_unsupported:
        status: SourceStatusLiteral = "unsupported"
    elif not code_supported:
        status = "planned"
    elif known_gap:
        # 已知缺口优先于校准结果——partial 比 supported 更准确
        status = "partial"
    elif calibration is None:
        status = "implemented_uncalibrated"
    elif calibration.result == CalibrationResult.SUCCESS:
        status = "supported"
    elif calibration.result == CalibrationResult.PARTIAL:
        status = "partial"
    else:
        # FAILED 校准 → 仍是 implemented_uncalibrated（需要重做校准）
        status = "implemented_uncalibrated"

    return CapabilityEntry(
        platform=platform,
        source_type=source_type,
        status=status,
        requires_login=requires_login,
        login_verifiable=login_verifiable,
        default_cadence=default_cadence,
        calibrated_at=calibrated_at,
        calibration_result=calibration_result,
        known_gap=known_gap,
    )


def can_enter_fetch_queue(entry: CapabilityEntry) -> bool:
    """UI / scheduler 判断该 source 是否可进入采集队列。"""
    return entry.status in ("supported", "partial")
