"""Operational status projections for task-manager rows."""
from __future__ import annotations

from typing import TypedDict

from backend.domain.json_types import JsonObject
from backend.domain.tasks import Task, TaskStatus
from backend.read_models.operations import TaskOperation, _failure_diagnostic


class TaskProgress(TypedDict):
    status: object
    processed: int
    total: object
    percent: int
    outputs: int
    current_stage: object
    stages: object


def progress(task: Task) -> TaskProgress:
    artifacts: JsonObject = task.artifacts_summary
    total = artifacts.get("total")
    processed = int(artifacts.get("processed") or 0)
    percent = artifacts.get("percent")
    if percent is None:
        try:
            total_int = int(total) if total is not None else 0
        except (TypeError, ValueError):
            total_int = 0
        percent = 100 if total_int <= 0 and task.status == TaskStatus.SUCCESS else (
            min(100, round(processed / total_int * 100)) if total_int > 0 else 0
        )
    return {
        "status": artifacts.get("status") or task.status.value,
        "processed": processed,
        "total": total,
        "percent": int(percent),
        "outputs": int(artifacts.get("outputs") or 0),
        "current_stage": artifacts.get("current_stage"),
        "stages": artifacts.get("stages") or [],
    }


def next_action_label(task: Task, operation: TaskOperation) -> str:
    diagnostic = _connector_diagnostic(task)
    category = str(diagnostic.get("category") or "").lower()
    status = operation["status"]
    if category == "challenge_required":
        return "在专用 Chrome 中完成安全验证"
    if category == "page_load":
        return "检查页面加载和网络后重试"
    if category == "rate_limited":
        return "等待平台冷却后重试"
    if category == "command_execution":
        return "查看 OpenCLI 退出码和命令输出"
    if category == "browser_session":
        return "打开专用 Chrome，然后重新检查"
    if category == "auth_required" or status == "auth_required":
        return "去登录，然后重新检查"
    if status == "deferred":
        return "等待下次运行，或确认是否需要手动重跑"
    if status in {"failed", "blocked"}:
        return str(diagnostic.get("action") or "查看错误后重跑")
    if status == "retrying":
        return "等待自动重试"
    return "无需操作"


def task_error_message(task: Task) -> str | None:
    artifacts: JsonObject = task.artifacts_summary
    diagnostic = _connector_diagnostic(task)
    return _first_text(
        diagnostic.get("message"),
        diagnostic.get("summary"),
        artifacts.get("error_message"),
        artifacts.get("error_summary"),
        artifacts.get("error_reason"),
        artifacts.get("error"),
        artifacts.get("reason"),
        task.error_reason,
    )


def _connector_diagnostic(task: Task) -> JsonObject:
    return _failure_diagnostic(task.artifacts_summary)


def _first_text(*values: object) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None
