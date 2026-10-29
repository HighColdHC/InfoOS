"""Pure interpretation of terminal media-download evidence."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


_RESOLVED_X_NO_MEDIA_REASONS = frozenset(
    {"skipped_no_media_found", "x_media_download_no_media_found"}
)


def is_resolved_x_no_media_outcome(
    *,
    source_platform: Any,
    download_status: Any,
    candidate_count: Any,
    reasons: Iterable[Any] = (),
) -> bool:
    """Return whether an explicit empty X download is a resolved, audited outcome."""
    if str(source_platform or "").strip().lower() != "x":
        return False
    try:
        no_candidates = int(candidate_count) == 0
    except (TypeError, ValueError):
        return False
    normalized_reasons = {
        str(reason).strip() for reason in reasons if str(reason).strip()
    }
    return (
        no_candidates
        and str(download_status or "").strip() == "skipped_no_media_found"
        and bool(normalized_reasons)
        and normalized_reasons <= _RESOLVED_X_NO_MEDIA_REASONS
    )
