from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.types import Task, TaskStatus


def handle_pipeline_reconcile(task: Task, base_dir: Path) -> dict[str, Any]:
    """Repair durable omissions without pretending queueing itself is work success."""
    if task.target_kind != "global":
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"reason": f"pipeline reconciliation does not support {task.target_kind} targets"},
        }
    from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
    from backend.application.workflows.audio_retention import cleanup_retained_audio
    from backend.application.workflows.auth_recovery import (
        drain_auth_recovery_notifications,
    )

    from backend.application.workflows.auth_qr import drain_qr_rounds
    from backend.application.workflows.agent_watchdog import check_agent_writeback
    from backend.domain.timestamps import utc_now_seconds_z

    artifacts = reconcile_pipeline(base_dir)
    artifacts["audio_retention"] = cleanup_retained_audio(base_dir)
    artifacts["auth_recovery_notifications"] = (
        drain_auth_recovery_notifications(base_dir)
    )
    drain_qr_rounds(base_dir, now=utc_now_seconds_z())
    artifacts["agent_watchdog"] = check_agent_writeback(base_dir)
    return {
        "status": TaskStatus.SUCCESS.value,
        "artifacts": artifacts,
    }
