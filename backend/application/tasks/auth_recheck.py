"""One bounded auth recheck inside an admitted FETCH's browser resource lifetime."""
from __future__ import annotations

from dataclasses import replace
import os
import threading
from pathlib import Path

from backend.application.setup.verify import confirmed_login_required, probe_platform, supports_platform_probe
from backend.application.tasks.execution_clock import add_seconds, current_iso
from backend.application.tasks.types import Task
from backend.application.workflows.acquisition_failures import AcquisitionFailureError
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.task_resources import ResourceLease
from backend.infrastructure.opencli.tab_lease import normalize_opencli_site, opencli_tab_lease
from backend.infrastructure.runtime.setup_state import AccountStatus
from backend.infrastructure.security.redaction import summarize_subprocess_output
from backend.infrastructure.storage.auth_recheck import AuthRecheckRepository


AUTH_RECHECK_WINDOW_SECONDS = 600


def recheck_auth_failure(
    error: AcquisitionFailureError, task: Task, base_path: Path, *,
    platform: str, worker_id: str, now: str, resource: ResourceLease | None,
    stop_event: threading.Event | None = None,
) -> AcquisitionFailureError:
    evidence = {"platform": platform, "outcome": "unknown", "summary": "复查未完成"}
    repo = AuthRecheckRepository(base_path)
    try:
        window = max(0, int(os.environ.get("INFOOS_AUTH_RECHECK_WINDOW_SECONDS", AUTH_RECHECK_WINDOW_SECONDS)))
    except ValueError:
        window = AUTH_RECHECK_WINDOW_SECONDS
    try:
        if stop_event is not None and stop_event.is_set():
            evidence['summary'] = '复查未完成：worker 正在停止'
        elif not supports_platform_probe(platform):
            evidence.update(outcome="unsupported", summary="复查未完成：平台暂无登录探针")
        elif repo.recently_verified(platform, since=add_seconds(now, -window), now=now):
            evidence.update(outcome="suppressed", summary="近期登录复查已通过，再次未登录不重复复查，要求人工登录")
        elif resource is None or resource.resource_kind != "browser_site" or resource.resource_key != normalize_opencli_site(platform) or not repo.owns_browser_resource(
            task.task_id, worker_id, resource, now=current_iso(),
        ):
            evidence["summary"] = "复查未完成：浏览器资源租约不可用"
        else:
            with opencli_tab_lease(platform, close_tabs=False, timeout_seconds=5):
                account = probe_platform(platform)
            if not repo.owns_browser_resource(task.task_id, worker_id, resource, now=current_iso()):
                raise RuntimeError("登录复查期间浏览器资源租约失效")
            evidence["probe_status"] = account.status.value
            evidence["probe_summary"] = summarize_subprocess_output(None, account.error)
            if account.status is AccountStatus.VERIFIED:
                evidence.update(outcome="verified", summary="登录复查通过：平台功能探针确认已登录")
            elif confirmed_login_required(account):
                evidence.update(outcome="not_logged_in", summary="登录复查确认未登录")
            else:
                evidence["summary"] = "复查未完成：登录探针无法确认登录状态"
    except Exception as exc:  # noqa: BLE001 - Optional browser login probe failures must preserve the original acquisition failure and attach probe evidence
        evidence["probe_summary"] = summarize_subprocess_output(None, f"{type(exc).__name__}: {exc}")
    if stop_event is not None and stop_event.is_set():
        evidence.update(outcome='unknown', summary='复查未完成：worker 正在停止')
    failure = error.failure
    diagnostic = {**failure.diagnostic, "auth_recheck": evidence}
    if evidence["outcome"] in {"verified", "unknown"}:
        diagnostic.update(category="command_execution", original_failure_reason=failure.reason.value)
        failure = replace(failure, reason=FetchFailureReason.OPENCLI_COMMAND_FAILED,
                          retryable=True, user_action=None)
    return AcquisitionFailureError(replace(failure, diagnostic=diagnostic))
