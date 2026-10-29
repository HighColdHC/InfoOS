"""Application resource lookup and compatibility value imports."""
from __future__ import annotations

from backend.domain.tasks import Task, TaskType
from backend.domain.task_resources import (
    ResourceRequest as ResourceRequest, ResourceLease as ResourceLease, AdmittedTask as AdmittedTask, new_resource_owner_token as new_resource_owner_token,
)
from backend.infrastructure.storage.repositories import SourceRepository


def platform_scope_for_task(task: Task, base_dir: str) -> str | None:
    if task.task_type is not TaskType.FETCH or task.target_kind != "source":
        return None
    if (
        task.artifacts_summary.get("capture_id")
        and task.artifacts_summary.get("capture_adapter") == "web"
    ):
        return None
    source = SourceRepository(base_dir).get_source(task.target_id)
    platform = str((source or {}).get("platform") or "").strip().lower()
    if platform == "rss" and source.get("source_type") == "feed":
        return f"rss:{source['source_id']}"
    return platform or None
