from __future__ import annotations

from backend.domain.calibration.builtin_matrix import BUILTIN_CAPABILITIES
from backend.domain.sources.configuration import PLATFORM_CADENCE_OPTIONS, SUPPORTED_EXECUTION
from backend.domain.sources.registry import (
    capability_catalog,
    configurable_platforms,
    source_browser_platforms,
)


def test_platform_capability_projections_are_consistent():
    configurable = {entry.platform for entry in configurable_platforms()}
    catalog = {entry["platform"]: entry for entry in capability_catalog()}

    assert configurable == {platform for platform, _ in SUPPORTED_EXECUTION}
    assert {(entry.platform, entry.source_type) for entry in BUILTIN_CAPABILITIES} == SUPPORTED_EXECUTION
    assert set(PLATFORM_CADENCE_OPTIONS) >= configurable
    assert source_browser_platforms() == {
        platform for platform in configurable if catalog[platform]["requires_browser_session"]
    }


def test_capability_catalog_contains_ui_source_form_contract():
    catalog = {entry["platform"]: entry for entry in capability_catalog()}

    assert catalog["rss"]["label"] == "RSS"
    rss_feed = catalog["rss"]["source_types"][0]
    assert rss_feed["source_type"] == "feed"
    assert rss_feed["default_cadence"] == "30min"
    assert rss_feed["default_cadence_hours"] == 0.5
    assert rss_feed["identifier_mode"] == "text"
    zsxq_topics = catalog["zsxq"]["source_types"][0]
    assert zsxq_topics["identifier_mode"] == "select"
    assert [option["value"] for option in zsxq_topics["identifier_options"]] == [
        "all", "ai", "xiaohongshu", "douyin", "gongzhonghao"
    ]
    producthunt_feed = catalog["producthunt"]["source_types"][0]
    assert [option["value"] for option in producthunt_feed["identifier_options"]] == ["today", "posts"]
    assert catalog["youtube"]["cadence_options"] == [0.5, 1, 5, 6, 24]
