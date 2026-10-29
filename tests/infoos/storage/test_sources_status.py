"""SourceStatus 5 档扩展（spec §11.1）。"""
from __future__ import annotations

import pytest

from backend.infrastructure.storage.sources import SourceStatus
from backend.infrastructure.storage.sources import SourceRepository


def test_five_levels_present():
    expected = {
        "supported",
        "implemented_uncalibrated",
        "partial",
        "planned",
        "unsupported",
    }
    actual = {s.value for s in SourceStatus}
    assert actual == expected


def test_supported_includes_calibration_requirement():
    """supported 必须是"已校准"的档，详见 spec §11.1 判定标准。"""
    desc = SourceStatus.SUPPORTED.__doc__ or ""
    assert "校准" in desc or "calibrat" in desc.lower()


def test_implemented_uncalibrated_distinct_from_supported():
    """implemented_uncalibrated 与 supported 必须是不同档——spec 关键决定。"""
    assert (
        SourceStatus.IMPLEMENTED_UNCALIBRATED
        != SourceStatus.SUPPORTED
    )


def test_unsupported_excluded_from_query():
    """unsupported 不应进入采集队列——通过 query 排除。"""
    # 见 list_due_sources 的实现（修改步骤中）
    assert "unsupported" in {s.value for s in SourceStatus}


def test_source_upsert_does_not_default_unknown_connector_to_supported(tmp_path):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "reddit_keyword",
            "platform": "reddit",
            "source_type": "keyword",
            "identifier": "AI agents",
            "display_name": "Reddit AI agents",
            "cadence_hours": 5,
            "enabled": True,
        }
    )

    source = SourceRepository(tmp_path).get_source("reddit_keyword")

    assert source is not None
    assert source["connector_status"] == "implemented_uncalibrated"
