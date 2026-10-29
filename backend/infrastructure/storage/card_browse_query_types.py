"""Typed browse aggregation and opportunity projections."""
from typing import NotRequired, TypedDict


class BrowseSource(TypedDict):
    source_id: str
    name: str
    platform: str
    card_count: int
    evaluated_count: int
    passed_count: int


class BrowsePlatform(TypedDict):
    platform: str
    card_count: int
    label: NotRequired[str]


class BrowseLimits(TypedDict):
    platforms: int | None
    sources: int | None


class BrowseTruncated(TypedDict):
    platforms: bool
    sources: bool


class BrowseFacets(TypedDict):
    platforms: list[BrowsePlatform]
    sources: list[BrowseSource]
    limits: BrowseLimits
    truncated: BrowseTruncated


class PreviewOpportunity(TypedDict):
    has_breakdown: bool
    next_step: str
