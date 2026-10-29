"""Feed-provider policy and embedded-content selection rules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


FeedProvider = Literal["generic"]
FeedContentMode = Literal["feed_preferred", "feed_only"]
FeedContentOrigin = Literal[
    "content_encoded",
    "atom_content",
    "description",
    "atom_summary",
    "missing",
]

DEFAULT_FEED_PROVIDER: FeedProvider = "generic"
DEFAULT_FEED_CONTENT_MODE: FeedContentMode = "feed_preferred"
FEED_PROVIDERS = frozenset({"generic"})
FEED_CONTENT_MODES = frozenset({"feed_preferred", "feed_only"})


@dataclass(frozen=True)
class SelectedFeedContent:
    content: str | None
    origin: FeedContentOrigin


def feed_policy(metadata: dict[str, Any] | None) -> tuple[FeedProvider, FeedContentMode]:
    values = metadata or {}
    provider = str(values.get("feed_provider") or DEFAULT_FEED_PROVIDER).strip().lower()
    mode = str(values.get("feed_content_mode") or DEFAULT_FEED_CONTENT_MODE).strip().lower()
    if provider not in FEED_PROVIDERS:
        raise ValueError("feed_provider must be generic")
    if mode not in FEED_CONTENT_MODES:
        raise ValueError("feed_content_mode must be feed_preferred or feed_only")
    return provider, mode  # type: ignore[return-value]


def validate_feed_policy(
    platform: str,
    source_type: str,
    metadata: dict[str, Any] | None,
) -> tuple[FeedProvider, FeedContentMode]:
    provider, mode = feed_policy(metadata)
    is_feed = platform == "rss" and source_type == "feed"
    if mode == "feed_only" and not is_feed:
        raise ValueError("feed_content_mode feed_only is only valid for rss feed sources")
    if is_feed and provider == "generic" and mode != "feed_preferred":
        raise ValueError("rss feed sources using generic must use feed_content_mode feed_preferred")
    return provider, mode


def versioned_feed_policy(
    platform: str | None,
    source_type: str | None,
    metadata: dict[str, Any] | None,
) -> dict[str, str]:
    """Return only collection-affecting, non-legacy feed policy fields."""
    if str(platform or "").strip() != "rss" or str(source_type or "").strip() != "feed":
        return {}
    provider, mode = feed_policy(metadata)
    if provider == DEFAULT_FEED_PROVIDER and mode == DEFAULT_FEED_CONTENT_MODE:
        return {}
    return {
        "feed_provider": provider,
        "feed_content_mode": mode,
    }


def select_rss2_content(
    *,
    provider: FeedProvider,
    mode: FeedContentMode,
    content_encoded: str | None,
    description: str | None,
) -> SelectedFeedContent:
    if content_encoded:
        return SelectedFeedContent(content_encoded, "content_encoded")
    if mode != "feed_only" and description:
        return SelectedFeedContent(description, "description")
    return SelectedFeedContent(None, "missing")


def select_atom_content(
    *,
    provider: FeedProvider,
    mode: FeedContentMode,
    content: str | None,
    summary: str | None,
) -> SelectedFeedContent:
    if content:
        return SelectedFeedContent(content, "atom_content")
    if mode != "feed_only" and summary:
        return SelectedFeedContent(summary, "atom_summary")
    return SelectedFeedContent(None, "missing")
