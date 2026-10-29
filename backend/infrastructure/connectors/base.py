from __future__ import annotations

from dataclasses import dataclass

from backend.domain.models.raw_item import RawItem


@dataclass
class FetchResult:
    items: list[RawItem]
    next_cursor: str | None
    visible_count: int = 0
    new_count: int = 0
    dedup_skipped_count: int = 0
    acquisition: dict | None = None

    def __post_init__(self) -> None:
        if self.new_count == 0 and self.items:
            self.new_count = len(self.items)
        if self.visible_count == 0:
            self.visible_count = self.new_count
        if self.dedup_skipped_count == 0:
            self.dedup_skipped_count = max(0, self.visible_count - self.new_count)
