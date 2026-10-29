from __future__ import annotations

from backend.domain.timestamps import utc_now_seconds_z

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Callable

from backend.application.enrichment.media.x_failures import classify_x_download_failure
from backend.application.workflows.browser_acquisition_batch import (
    active_browser_acquisition_batch,
    repair_browser_acquisition_batch,
)
from backend.application.usecases.browser_session import (
    cleanup_headless_browser_session,
    prepare_opencli_browser_session,
)
from backend.application.usecases.source_config import DEFAULT_INFOOS_CHROME_PROFILE_DIR
from backend.infrastructure.browser import session_state
from backend.infrastructure.opencli.tab_lease import opencli_tab_lease
from backend.infrastructure.security.redaction import summarize_subprocess_output

_INFOOS_PROFILE_ALIAS = "infoos-fetch"
_BROWSER_SESSION_REASON = "x_media_download_browser_session"


def opencli_browser_lock():
    """Compatibility seam backed by the new per-site lease, not the old global lock."""
    return opencli_tab_lease("twitter")


@dataclass(frozen=True)
class XMediaDownloadLifecycleResult:
    command_result: Any | None
    failure_reason: str | None
    details: dict[str, Any]
    attempt_dir: Path | None = None


def run_x_media_download_lifecycle(
    *,
    tweet_url: str,
    staging_root: Path,
    command_runner: Callable[[str, Path], Any],
) -> XMediaDownloadLifecycleResult:
    """Prepare and run at most two serialized X media download attempts."""
    prepare_outcomes: list[str] = []
    profile_alias: str | None = None
    command_attempts = 0
    final_result: Any | None = None
    final_reason: str | None = None
    final_attempt_dir: Path | None = None
    browser_batch = active_browser_acquisition_batch()

    for cycle in range(2):
        cycle_result: Any | None = None
        cycle_reason: str | None = None
        attempt_dir: Path | None = None
        try:
            try:
                if cycle == 0 and browser_batch is not None:
                    prepared = browser_batch.prepare_result
                elif browser_batch is not None:
                    prepared = repair_browser_acquisition_batch(
                        browser_batch,
                        original_diagnostic={
                            "category": "browser_session",
                            "reason": _BROWSER_SESSION_REASON,
                        },
                        retried_at=utc_now_seconds_z(),
                    )
                else:
                    prepared = prepare_opencli_browser_session(
                        open_url=tweet_url,
                        wait_seconds=20,
                        probe_x_login=False,
                        must_rebind=True,
                    )
            except Exception:  # noqa: BLE001 - Browser preparation and repair subprocess failures must become a download lifecycle failure
                prepare_outcomes.append("exception")
                cycle_reason = _BROWSER_SESSION_REASON
            else:
                profile_alias = _validated_profile_alias(prepared)
                prepare_status = prepared["status"]
                prepare_outcomes.append("ready" if profile_alias else prepare_status)
                if profile_alias is None:
                    cycle_reason = _BROWSER_SESSION_REASON
                else:
                    staging_root.mkdir(parents=True, exist_ok=True)
                    attempt_dir = Path(tempfile.mkdtemp(prefix=".opencli-attempt-", dir=staging_root))
                    attempt_dir.chmod(0o700)
                    try:
                        with opencli_browser_lock():
                            command_attempts += 1
                            try:
                                cycle_result = command_runner(profile_alias, attempt_dir)
                            except (FileNotFoundError, subprocess.TimeoutExpired):
                                cycle_reason = "x_media_download_command_unavailable_or_timeout"
                            finally:
                                _secure_attempt_files(attempt_dir)
                    except Exception:  # noqa: BLE001 - OpenCLI lease and command failures must become a download lifecycle failure
                        cycle_reason = _BROWSER_SESSION_REASON
        finally:
            if browser_batch is None:
                try:
                    cleanup_headless_browser_session()
                except Exception:  # noqa: BLE001 - Browser cleanup failures must not replace a completed download result
                    if cycle_result is None:
                        cycle_reason = _BROWSER_SESSION_REASON

        final_result = cycle_result
        if cycle_reason is not None:
            _cleanup_attempt(attempt_dir)
            final_reason = cycle_reason
            break
        if cycle_result is None:
            _cleanup_attempt(attempt_dir)
            final_reason = _BROWSER_SESSION_REASON
            break
        if cycle_result.returncode == 0:
            final_reason = None
            final_attempt_dir = attempt_dir
            break

        diagnostic = summarize_subprocess_output(cycle_result.stdout, cycle_result.stderr)
        final_reason = classify_x_download_failure(diagnostic)
        _cleanup_attempt(attempt_dir)
        if final_reason != _BROWSER_SESSION_REASON or cycle == 1:
            break

    details: dict[str, Any] = {
        "strategy": "opencli",
        "profile_alias": profile_alias,
        "preflight_outcome": prepare_outcomes[0],
        "repair_outcome": prepare_outcomes[1] if len(prepare_outcomes) > 1 else "not_attempted",
        "prepare_attempt_count": len(prepare_outcomes),
        "download_attempt_count": command_attempts,
    }
    if final_result is not None:
        details["exit_code"] = int(final_result.returncode)
        if final_result.returncode != 0:
            stderr_summary = summarize_subprocess_output(final_result.stdout, final_result.stderr)
            if stderr_summary:
                details["stderr_summary"] = stderr_summary
    return XMediaDownloadLifecycleResult(final_result, final_reason, details, final_attempt_dir)


def _secure_attempt_files(attempt_dir: Path) -> None:
    for path in attempt_dir.rglob("*"):
        if path.is_file():
            path.chmod(0o600)


def _cleanup_attempt(attempt_dir: Path | None) -> None:
    if attempt_dir is not None:
        shutil.rmtree(attempt_dir, ignore_errors=True)


def _validated_profile_alias(prepared: dict[str, Any]) -> str | None:
    if prepared.get("status") != "ready":
        return None
    profile = prepared.get("profile")
    if isinstance(profile, dict) and _valid_prepared_profile(profile):
        return _INFOOS_PROFILE_ALIAS

    state = session_state.read()
    if state is None or not session_state.is_alive(state):
        return None
    try:
        expected_dir = Path(DEFAULT_INFOOS_CHROME_PROFILE_DIR).expanduser()
        actual_dir = Path(state.profile_dir).expanduser()
    except (TypeError, ValueError):
        return None
    if (
        state.alias != _INFOOS_PROFILE_ALIAS
        or state.last_health_status != "ready"
        or actual_dir != expected_dir
    ):
        return None
    return state.alias


def _valid_prepared_profile(profile: dict[str, Any]) -> bool:
    return (
        profile.get("alias") == _INFOOS_PROFILE_ALIAS
        and profile.get("bound") is True
        and profile.get("binding_status") == "ready"
    )
