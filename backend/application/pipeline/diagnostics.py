from __future__ import annotations

import json
from pathlib import Path

from backend.application.pipeline.run_result import ErrorEvent


def diagnostics_dir(base_dir: Path) -> Path:
    return base_dir / "data" / "diagnostics"


def append_error(base_dir: Path, error: ErrorEvent) -> Path:
    path = diagnostics_dir(base_dir) / "errors.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(error.to_dict(), ensure_ascii=False, sort_keys=True))
        fh.write("\n")
    return path
