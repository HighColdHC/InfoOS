from __future__ import annotations

from copy import deepcopy

import pytest

from backend.domain.sources.versioning import source_fetch_input_version


def _source() -> dict[str, object]:
    return {
        "source_id": "source-x-ai",
        "platform": "x",
        "source_type": "creator",
        "identifier": "openai",
        "connector_id": "opencli-x",
        "display_name": "OpenAI",
        "notes": "Primary account",
        "cadence_hours": 6,
        "enabled": True,
        "topics": ["ai"],
        "purpose": "monitor",
        "default_domain_id": "legacy-domain",
        "raw_metadata": {
            "max_items_per_run": 20,
            "auto_enrich_media": True,
            "command": ["twitter", "tweets", "openai", "--limit", "20"],
            "items_path": "data.items",
            "field_mapping": {"text": "text", "id": "id"},
            "metadata_mapping": {"likes": "public_metrics.like_count"},
            "result_order": "newest_first",
            "requires_browser_session": True,
        },
    }


def test_display_schedule_and_activation_changes_do_not_change_fetch_identity() -> None:
    source = _source()
    changed = deepcopy(source)
    changed.update(
        {
            "display_name": "OpenAI Updates",
            "notes": "Renamed without changing collection",
            "cadence_hours": 24,
            "enabled": False,
            "topics": ["agents"],
            "purpose": "research",
            "default_domain_id": "another-legacy-domain",
        }
    )

    version = source_fetch_input_version(source)

    assert version.startswith("sha256:")
    assert source_fetch_input_version(changed) == version


@pytest.mark.parametrize(
    ("location", "key", "changed_value"),
    [
        ("source", "platform", "youtube"),
        ("source", "source_type", "keyword"),
        ("source", "identifier", "anthropicai"),
        ("source", "connector_id", "opencli-x-v2"),
        ("raw_metadata", "max_items_per_run", 50),
        ("raw_metadata", "auto_enrich_media", False),
        ("raw_metadata", "feed_url", "https://example.com/feed.xml"),
        ("raw_metadata", "command", ["twitter", "tweets", "openai", "--limit", "50"]),
        ("raw_metadata", "items_path", "payload.items"),
        ("raw_metadata", "field_mapping", {"text": "body", "id": "id"}),
        ("raw_metadata", "metadata_mapping", {"likes": "likes"}),
        ("raw_metadata", "detail_command", ["twitter", "tweet", "{url}"]),
        ("raw_metadata", "result_order", "oldest_first"),
        ("raw_metadata", "requires_browser_session", False),
    ],
)
def test_collection_configuration_changes_fetch_identity(
    location: str,
    key: str,
    changed_value: object,
) -> None:
    source = _source()
    changed = deepcopy(source)
    target = changed if location == "source" else changed["raw_metadata"]
    assert isinstance(target, dict)
    target[key] = changed_value

    assert source_fetch_input_version(changed) != source_fetch_input_version(source)


def test_zsxq_search_filters_change_fetch_identity() -> None:
    source = {
        **_source(),
        "platform": "zsxq",
        "source_type": "keyword",
        "identifier": "AI 网站出海",
    }
    changed = deepcopy(source)
    metadata = changed["raw_metadata"]
    assert isinstance(metadata, dict)
    metadata["zsxq_search"] = {
        "sort": "newest",
        "content_type": "essence",
        "keywords": ["web出海", "AI网站出海"],
        "start_date": "2024-07-30",
        "end_date": "2026-07-30",
    }

    assert source_fetch_input_version(changed) != source_fetch_input_version(source)


def test_legacy_non_feed_fingerprint_is_unchanged_and_ignores_feed_fields() -> None:
    source = _source()
    explicit_defaults = deepcopy(source)
    metadata = explicit_defaults["raw_metadata"]
    assert isinstance(metadata, dict)
    metadata.update(
        {
            "feed_provider": "generic",
            "feed_content_mode": "feed_preferred",
        }
    )
    expected = "sha256:dda231956ce2df5d8cc62761ed4efb6f834cfbcd0961da450b490ce7f1e9e022"
    assert source_fetch_input_version(source) == expected
    assert source_fetch_input_version(explicit_defaults) == expected

    youtube = {
        **source,
        "platform": "youtube",
        "source_type": "channel",
        "identifier": "@openai",
    }
    youtube_with_feed_fields = deepcopy(youtube)
    metadata = youtube_with_feed_fields["raw_metadata"]
    assert isinstance(metadata, dict)
    metadata.update(
        {
            "feed_provider": "generic",
            "feed_content_mode": "feed_only",
        }
    )
    assert source_fetch_input_version(youtube_with_feed_fields) == source_fetch_input_version(
        youtube
    )


def test_rss_default_policy_keeps_legacy_fingerprint() -> None:
    source = {
        "source_id": "rss",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed",
        "connector_id": "rss",
        "raw_metadata": {
            "max_items_per_run": 20,
            "auto_enrich_media": True,
        },
    }
    explicit_default = deepcopy(source)
    metadata = explicit_default["raw_metadata"]
    assert isinstance(metadata, dict)
    metadata.update(
        {
            "feed_provider": "generic",
            "feed_content_mode": "feed_preferred",
        }
    )

    legacy = "sha256:86acaf421d93ba80d36c1693ce8590bbea391b7e296913194d89e49de480af9a"
    assert source_fetch_input_version(source) == legacy
    assert source_fetch_input_version(explicit_default) == legacy


def test_mapping_insertion_order_and_serialized_metadata_do_not_change_fetch_identity() -> None:
    source = _source()
    reordered = deepcopy(source)
    raw_metadata = reordered["raw_metadata"]
    assert isinstance(raw_metadata, dict)
    raw_metadata["field_mapping"] = {"id": "id", "text": "text"}
    raw_metadata["metadata_mapping"] = {
        "nested": {"second": ["a", "b"], "first": "value"},
        "likes": "public_metrics.like_count",
    }

    original = deepcopy(source)
    original_raw_metadata = original["raw_metadata"]
    assert isinstance(original_raw_metadata, dict)
    original_raw_metadata["metadata_mapping"] = {
        "likes": "public_metrics.like_count",
        "nested": {"first": "value", "second": ["a", "b"]},
    }

    serialized = deepcopy(reordered)
    serialized["raw_metadata"] = (
        '{"requires_browser_session":true,"result_order":"newest_first",'
        '"metadata_mapping":{"nested":{"second":["a","b"],"first":"value"},'
        '"likes":"public_metrics.like_count"},'
        '"field_mapping":{"id":"id","text":"text"},"items_path":"data.items",'
        '"command":["twitter","tweets","openai","--limit","20"],'
        '"auto_enrich_media":true,"max_items_per_run":20}'
    )

    version = source_fetch_input_version(original)

    assert source_fetch_input_version(reordered) == version
    assert source_fetch_input_version(serialized) == version


def test_sensitive_credentials_and_transient_readiness_do_not_change_fetch_identity() -> None:
    source = _source()
    changed = deepcopy(source)
    changed.update(
        {
            "token": "top-level-token",
            "cookie": "top-level-cookie",
            "readiness": "ready",
            "last_readiness_check_at": "2026-07-16T12:00:00Z",
        }
    )
    raw_metadata = changed["raw_metadata"]
    assert isinstance(raw_metadata, dict)
    raw_metadata.update(
        {
            "access_token": "secret",
            "cookies": {"session": "secret"},
            "browser_readiness": {"status": "ready"},
            "login_checked_at": "2026-07-16T12:00:00Z",
        }
    )

    assert source_fetch_input_version(changed) == source_fetch_input_version(source)


def test_equivalent_scalar_and_json_metadata_representations_share_fetch_identity() -> None:
    source = _source()
    equivalent = deepcopy(source)
    equivalent.update(
        {
            "platform": " x ",
            "source_type": " creator ",
            "identifier": " openai ",
            "connector_id": " opencli-x ",
        }
    )
    raw_metadata = equivalent["raw_metadata"]
    assert isinstance(raw_metadata, dict)
    raw_metadata["max_items_per_run"] = "20"
    raw_metadata["auto_enrich_media"] = "true"
    raw_metadata["requires_browser_session"] = "true"

    assert source_fetch_input_version(equivalent) == source_fetch_input_version(source)
