"""Match query boundaries to raw_items.first_seen_at's UTC-second format."""
from backend.domain.timestamps import format_utc_seconds_z, parse_utc


def entry_key(value: str) -> str:
    return format_utc_seconds_z(parse_utc(value))
