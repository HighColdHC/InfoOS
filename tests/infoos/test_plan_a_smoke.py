"""Plan A 端到端冒烟：失败分类 + 校准升级 + 队列进入。"""
from __future__ import annotations

from datetime import datetime

import pytest

from backend.domain.failures import (
    FetchFailureReason,
    classify_failure,
    get_retry_policy,
    get_notification,
    FetchContext,
    ClassificationError,
)
from backend.domain.calibration import (
    Calibration,
    CalibrationResult,
    record_calibration,
    compute_capability,
    can_enter_fetch_queue,
    REQUIRED_LOGIN,
)


def test_login_failure_full_pipeline():
    """401 → platform_not_logged_in → no-retry + desktop_and_web notification。"""
    err = RuntimeError("login required")
    ctx = FetchContext(platform="x", source_type="creator", http_status=401)

    # 1. 分类
    reason = classify_failure(err, ctx)
    assert reason == FetchFailureReason.PLATFORM_NOT_LOGGED_IN

    # 2. 重试策略
    policy = get_retry_policy(reason)
    assert policy.max_attempts == 0

    # 3. 用户通知
    notif = get_notification(reason)
    assert notif.level.value == "desktop_and_web"


def test_rate_limited_full_pipeline():
    """429 → rate_limited → exponential backoff with source threshold。"""
    err = RuntimeError("too many requests")
    ctx = FetchContext(platform="x", source_type="keyword", http_status=429)

    reason = classify_failure(err, ctx)
    assert reason == FetchFailureReason.RATE_LIMITED

    policy = get_retry_policy(reason)
    assert policy.max_attempts == 3
    assert policy.escalate_to_blocked is True


def test_calibration_lifecycle():
    """source 从 implemented_uncalibrated → supported。"""
    # 初始状态
    entry = compute_capability(
        platform="x", source_type="creator",
        code_supported=True, calibration=None,
    )
    assert entry.status == "implemented_uncalibrated"
    assert can_enter_fetch_queue(entry) is False

    # 校准成功
    cal = record_calibration(
        platform="x", source_type="creator", identifier="@openai",
        result=CalibrationResult.SUCCESS, notes="first real-source run",
    )
    entry = compute_capability(
        platform="x", source_type="creator",
        code_supported=True, calibration=cal,
    )
    assert entry.status == "supported"
    assert can_enter_fetch_queue(entry) is True
