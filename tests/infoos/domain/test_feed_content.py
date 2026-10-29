from __future__ import annotations

import pytest

from backend.domain.sources.feed_content import (
    feed_policy,
    select_atom_content,
    select_rss2_content,
    validate_feed_policy,
)


def test_feed_policy_defaults_preserve_generic_feed_behavior() -> None:
    assert feed_policy({}) == ("generic", "feed_preferred")
    assert select_rss2_content(
        provider="generic",
        mode="feed_preferred",
        content_encoded=None,
        description="RSS summary",
    ).origin == "description"
    assert select_atom_content(
        provider="generic",
        mode="feed_preferred",
        content=None,
        summary="Atom summary",
    ).origin == "atom_summary"


@pytest.mark.parametrize(
    ("platform", "source_type", "metadata", "message"),
    [
        (
            "rss",
            "feed",
            {"feed_provider": "generic", "feed_content_mode": "feed_only"},
            "using generic must use feed_content_mode feed_preferred",
        ),
        (
            "youtube",
            "channel",
            {"feed_provider": "generic", "feed_content_mode": "feed_only"},
            "only valid for rss feed",
        ),
    ],
)
def test_feed_policy_rejects_invalid_source_combinations(
    platform: str,
    source_type: str,
    metadata: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_feed_policy(platform, source_type, metadata)
