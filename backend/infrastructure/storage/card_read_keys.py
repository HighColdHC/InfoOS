"""Materialized equivalents of the card preview timestamp expressions."""
from backend.domain.timestamps import normalize_timestamp, timestamp_sort_key


def card_read_keys(published_at: str | None, first_seen_at: str | None) -> tuple[str, str | None]:
    published = normalize_timestamp(published_at)
    return (
        timestamp_sort_key(published_at, first_seen_at),
        (published.replace('Z', '') + '.000000')[:26] if published else None,
    )
