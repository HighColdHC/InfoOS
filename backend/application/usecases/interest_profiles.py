"""Validate and version agent-supplied interest profiles."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.domain.intelligence.profile import parse_profile, profile_hash
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository


def push_profile(base_dir: Path | str, markdown: str, *, source: str) -> dict[str, Any]:
    sections = parse_profile(markdown)
    repository = InterestProfileRepository(base_dir)
    with repository.connect_existing() as conn:
        try:
            conn.execute("begin immediate")
            profile, created = repository.insert_current(
                conn, markdown=markdown, sections=sections,
                content_hash=profile_hash(markdown), source=source,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {**profile, "created": created, "section_counts": {
        name: len(items) for name, items in profile["sections"].items()
    }}
