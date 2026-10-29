"""RawItemStore — filesystem-backed raw item storage helpers.

raw_store — module migrated to infoos in PR 4.8.
The corresponding infoos/storage/raw_store.py was deleted in this commit.
This file is now the sole source of truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from backend.domain.models.raw_item import RawItem


class RawItemStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def append(self, connector_id: str, items: Iterable[RawItem]) -> Path:
        connector_dir = self.base_dir / connector_id
        connector_dir.mkdir(parents=True, exist_ok=True)
        path = connector_dir / "raw_items.jsonl"

        with path.open("a", encoding="utf-8") as fh:
            for item in items:
                fh.write(item.model_dump_json())
                fh.write("\n")

        return path
