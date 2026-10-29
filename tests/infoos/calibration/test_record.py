"""Calibration record: 真实 source 校准记录（spec §11.2）。"""
from __future__ import annotations

from datetime import datetime

import pytest

from backend.domain.calibration.record import (
    Calibration,
    CalibrationResult,
    record_calibration,
)


def test_calibration_dataclass_fields():
    c = Calibration(
        platform="x",
        source_type="creator",
        identifier="@openai",
        result=CalibrationResult.SUCCESS,
        calibrated_at=datetime(2026, 6, 21, 10, 0),
        error_classification=None,
        notes="first successful real-source run",
    )
    assert c.platform == "x"
    assert c.result == CalibrationResult.SUCCESS


def test_calibration_result_enum():
    assert CalibrationResult.SUCCESS.value == "success"
    assert CalibrationResult.PARTIAL.value == "partial"
    assert CalibrationResult.FAILED.value == "failed"


def test_record_calibration_returns_calibration():
    c = record_calibration(
        platform="rss",
        source_type="feed",
        identifier="https://hnrss.org/newest",
        result=CalibrationResult.SUCCESS,
        notes="RSS calibration test",
    )
    assert isinstance(c, Calibration)
    assert c.platform == "rss"
    assert c.result == CalibrationResult.SUCCESS


def test_record_calibration_with_failure_includes_classification():
    c = record_calibration(
        platform="x",
        source_type="creator",
        identifier="@somebody",
        result=CalibrationResult.FAILED,
        error_classification="platform_not_logged_in",
        notes="login expired during fetch",
    )
    assert c.error_classification == "platform_not_logged_in"
