"""Built-in capability matrix（spec §11 总览表硬编码）。

本表是初始值，反映 2026-06-21 spec 拍板时的状态。后续真实校准
通过 record_calibration() 落库后，由 compute_capability() 用校准结果
覆盖本表。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from backend.domain.calibration.builtin_matrix import BUILTIN_CAPABILITIES
from backend.domain.calibration.matrix import compute_capability
from backend.domain.calibration.record import Calibration, CalibrationResult


def test_builtin_entries_are_projected_from_executable_registry():
    from backend.domain.sources.configuration import SUPPORTED_EXECUTION

    assert {(entry.platform, entry.source_type) for entry in BUILTIN_CAPABILITIES} == SUPPORTED_EXECUTION


def test_xiaohongshu_creator_uses_canonical_platform_name():
    xhs = [e for e in BUILTIN_CAPABILITIES if e.platform == "xiaohongshu" and e.source_type == "creator"]
    assert len(xhs) == 1
    assert xhs[0].status == "implemented_uncalibrated"


def test_rss_supported_no_login():
    rss = [e for e in BUILTIN_CAPABILITIES if e.platform == "rss"]
    assert len(rss) == 1
    entry = rss[0]
    assert entry.requires_login == "no"
    assert entry.login_verifiable == "n/a"


def test_x_creator_requires_login():
    x_creator = [
        e for e in BUILTIN_CAPABILITIES
        if e.platform == "x" and e.source_type == "creator"
    ]
    assert len(x_creator) == 1
    assert x_creator[0].requires_login == "required"


def test_youtube_home_feed_requires_login_without_changing_public_sources():
    by_type = {
        entry.source_type: entry
        for entry in BUILTIN_CAPABILITIES
        if entry.platform == "youtube"
    }

    assert by_type["home_feed"].requires_login == "required"
    assert by_type["home_feed"].login_verifiable == "verifiable"
    assert by_type["channel"].requires_login == "optional"
    assert by_type["keyword"].requires_login == "optional"


def test_calibration_overrides_builtin():
    """真实校准可以覆盖 BUILTIN_CAPABILITIES 的 status。"""
    builtin = next(
        e for e in BUILTIN_CAPABILITIES
        if e.platform == "x" and e.source_type == "creator"
    )
    assert builtin.status == "implemented_uncalibrated"

    calibration = Calibration(
        platform="x",
        source_type="creator",
        identifier="@openai",
        result=CalibrationResult.SUCCESS,
        calibrated_at=datetime(2026, 6, 21, 10, 0),
        error_classification=None,
    )
    overridden = compute_capability(
        platform=builtin.platform,
        source_type=builtin.source_type,
        code_supported=True,
        calibration=calibration,
    )
    assert overridden.status == "supported"
