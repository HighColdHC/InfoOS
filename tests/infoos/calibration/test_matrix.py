"""Capability matrix: 5 档判定函数（spec §11.1/11.2）。"""
from __future__ import annotations

from datetime import datetime

import pytest

from backend.domain.calibration.matrix import (
    CapabilityEntry,
    REQUIRED_LOGIN,
    OPTIONAL_LOGIN,
    NO_LOGIN,
    LOGIN_VERIFIABLE,
    LOGIN_NOT_APPLICABLE,
    compute_capability,
    can_enter_fetch_queue,
)
from backend.domain.calibration.record import Calibration, CalibrationResult


def _calibration(success: bool) -> Calibration | None:
    if not success:
        return None
    return Calibration(
        platform="x",
        source_type="creator",
        identifier="@test",
        result=CalibrationResult.SUCCESS,
        calibrated_at=datetime(2026, 6, 21),
        error_classification=None,
    )


def test_supported_requires_successful_calibration():
    entry = compute_capability(
        platform="x",
        source_type="creator",
        code_supported=True,
        calibration=_calibration(True),
    )
    assert entry.status == "supported"


def test_implemented_uncalibrated_when_code_supported_no_calibration():
    entry = compute_capability(
        platform="x",
        source_type="creator",
        code_supported=True,
        calibration=None,
    )
    assert entry.status == "implemented_uncalibrated"


def test_planned_when_not_code_supported():
    entry = compute_capability(
        platform="x",
        source_type="topic",
        code_supported=False,
        calibration=None,
    )
    assert entry.status == "planned"


def test_unsupported_when_explicitly_unsupported():
    entry = compute_capability(
        platform="xhs",
        source_type="creator",
        code_supported=False,
        calibration=None,
        explicit_unsupported=True,
    )
    assert entry.status == "unsupported"


def test_partial_when_code_supported_with_known_gap():
    entry = compute_capability(
        platform="x",
        source_type="keyword",
        code_supported=True,
        calibration=_calibration(True),
        known_gap="multi-keyword combinations not tested",
    )
    assert entry.status == "partial"
    assert "multi-keyword" in entry.known_gap


def test_can_enter_fetch_queue_supported():
    entry = compute_capability(
        platform="x",
        source_type="creator",
        code_supported=True,
        calibration=_calibration(True),
    )
    assert can_enter_fetch_queue(entry) is True


def test_can_enter_fetch_queue_implemented_uncalibrated_false():
    entry = compute_capability(
        platform="x",
        source_type="creator",
        code_supported=True,
        calibration=None,
    )
    assert can_enter_fetch_queue(entry) is False


def test_can_enter_fetch_queue_unsupported_false():
    entry = compute_capability(
        platform="xhs",
        source_type="creator",
        code_supported=False,
        calibration=None,
        explicit_unsupported=True,
    )
    assert can_enter_fetch_queue(entry) is False
