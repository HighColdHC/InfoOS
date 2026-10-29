import pytest

from backend.domain.sources.configuration_projection import database_source_to_connector_config
from backend.domain.sources.configuration_validation import validate_source_payload
from backend.domain.sources.keyword_search import keyword_search_options
from backend.domain.sources.versioning import source_fetch_input_version

BASELINE_HASHES = {
    "xiaohongshu": "sha256:b02c5d4853259c1495b31ce6f016c18418fc98b8973c4894c91f3a16dac0b961",
    "douyin": "sha256:635f941bb9bb0b3874abfeafca7db089ff779479e13913b7f345bcafa77a2233",
}


@pytest.mark.parametrize("platform", BASELINE_HASHES)
def test_default_keyword_search_preserves_existing_command_and_hash(platform):
    source = {"source_id": "test", "platform": platform, "source_type": "keyword", "identifier": "AI", "max_items_per_run": 20}
    for raw_metadata in ({}, {"keyword_search": {}}, {"keyword_search": {"sort": "general", "publish_time": "any"}}):
        configured = {**source, "raw_metadata": raw_metadata}
        config = database_source_to_connector_config(configured)
        assert config["command"] == [platform, "search", "AI", "--limit", "20"]
        assert config["result_order"] == "newest_first"
        assert source_fetch_input_version(configured) == BASELINE_HASHES[platform]


@pytest.mark.parametrize("platform,sort", [("xiaohongshu", "latest"), ("xiaohongshu", "most-liked"), ("xiaohongshu", "most-commented"), ("xiaohongshu", "most-collected"), ("douyin", "latest"), ("douyin", "most-liked")])
def test_keyword_search_filters_change_command_order_and_version(platform, sort):
    source = {"source_id": "test", "platform": platform, "source_type": "keyword", "identifier": "AI", "max_items_per_run": 20,
              "raw_metadata": {"keyword_search": {"sort": sort, "publish_time": "week"}}}
    config = database_source_to_connector_config(source)
    assert config["command"] == [platform, "search", "AI", "--limit", "20", "--sort", sort, "--publish-time", "week"]
    assert config["result_order"] == ("newest_first" if sort == "latest" else "snapshot")
    assert source_fetch_input_version(source) != BASELINE_HASHES[platform]
    validate_source_payload({**source, "cadence_hours": 5})


@pytest.mark.parametrize("platform", BASELINE_HASHES)
def test_publish_time_only_changes_command_and_hash(platform):
    source = {"source_id": "test", "platform": platform, "source_type": "keyword", "identifier": "AI", "max_items_per_run": 20,
              "raw_metadata": {"keyword_search": {"publish_time": "half-year"}}}
    assert database_source_to_connector_config(source)["command"] == [platform, "search", "AI", "--limit", "20", "--publish-time", "half-year"]
    assert source_fetch_input_version(source) != BASELINE_HASHES[platform]
    assert keyword_search_options(platform, {"keyword_search": {"sort": " LATEST ", "publish_time": " DAY "}}) == {"sort": "latest", "publish_time": "day"}


@pytest.mark.parametrize("platform,options", [("douyin", {"sort": "most-commented"}), ("douyin", {"sort": "most-collected"}), ("xiaohongshu", {"sort": "earliest"}), ("xiaohongshu", {"publish_time": "month"}), ("douyin", []), ("douyin", {"sort": None})])
def test_invalid_keyword_search_filters_are_rejected(platform, options):
    with pytest.raises(ValueError, match="keyword_search"):
        validate_source_payload({"platform": platform, "source_type": "keyword", "identifier": "AI", "cadence_hours": 5, "raw_metadata": {"keyword_search": options}})
