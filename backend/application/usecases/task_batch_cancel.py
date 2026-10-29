from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from backend.application.usecases.task_cancel import cancel_task


def cancel_tasks(base_dir: Path | str, task_ids: list[str], *, reason: str) -> dict[str, Any]:
    """Each requested task owns its transaction and business result."""
    return {
        "ok": True,
        "results": [_cancel_one(base_dir, task_id, reason) for task_id in task_ids],
    }


def _cancel_one(base_dir: Path | str, task_id: str, reason: str) -> dict[str, Any]:
    try:
        return cancel_task(base_dir, task_id, reason=reason)
    except sqlite3.Error:
        return {"ok": False, "task_id": task_id, "code": "task_cancel_failed", "status_code": 500}
