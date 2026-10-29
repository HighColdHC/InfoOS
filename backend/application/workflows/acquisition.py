"""Durable source acquisition for one FETCH task.

FETCH is deliberately limited to connector I/O and the atomic persistence of
raw items, source cursor, and CARD_BUILD tasks.  Card construction belongs to
the next queue stage.
"""
from __future__ import annotations

from backend.domain.timestamps import utc_now_seconds_z

from random import Random
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from backend.application.tasks.planner import source_run_chain_id
from backend.application.tasks.types import LEGACY_INPUT_VERSION, Task
from backend.application.usecases.source_capability import resolve_source_capability, source_can_enter_fetch_queue
from backend.application.workflows.fetch_support import annotate_raw_items as _annotate_raw_items
from backend.application.workflows.fetch_support import build_connector as _build_connector
from backend.application.workflows.fetch_commit import SourceConfigurationChangedError, commit_fetch_result
from backend.domain.calibration.matrix import REQUIRED_LOGIN
from backend.domain.sources.douyin_home_feed import is_douyin_home_feed, next_session_at
from backend.domain.sources.acquisition import acquisition_artifacts
from backend.domain.timestamps import format_utc_seconds_z, parse_utc
from backend.domain.schedule.cadence import compute_next_run_at
from backend.domain.sources.configuration import database_source_to_connector_config
from backend.domain.sources.versioning import source_fetch_input_version
from backend.domain.sources.zsxq import opportunity_source_for_topics
from backend.infrastructure.storage.repositories import RawItemRepository, SourceCursorRepository, SourceRepository
from backend.infrastructure.storage.tasks import operations as task_storage
from backend.infrastructure.security.redaction import redact_secrets


def fetch_source(
    base_dir: Path | str,
    *,
    source_id: str,
    task: Task,
) -> dict[str, Any]:
    """Fetch one source into durable raw acquisition artifacts only."""
    base_path = Path(base_dir)
    from backend.application.workflows.acquisition_checkpoint_replay import matching_complete_checkpoint

    checkpoint = matching_complete_checkpoint(base_path, task, source_id=source_id)
    if checkpoint is not None:
        return {**checkpoint, "replayed": True}

    source = SourceRepository(base_path).get_source(source_id)
    if source is None:
        raise LookupError(f"Source not found: {source_id}")
    capability = resolve_source_capability(source)
    research_fetch = (
        source["source_scope"] == "research"
        and source["source_type"] == "research"
    )
    initial_fetch = task.artifacts_summary.get("initial_fetch") is True
    expected_version = _admitted_source_version(base_path, source, task)
    if initial_fetch and task.claimed_by is None:
        raise ValueError("initial_fetch_requires_durable_task")
    if initial_fetch:
        from backend.application.workflows.source_initial_fetch import ensure_task_wins_source_arbitration

        ensure_task_wins_source_arbitration(base_path, task)
    if (
        not source["enabled"]
        or capability.status in {"planned", "unsupported"}
        or (not source_can_enter_fetch_queue(source) and not initial_fetch)
    ):
        raise SourceAdmissionDeniedError(f"Source is disabled or unsupported: {source_id}")
    if initial_fetch and source["connector_status"] != "implemented_uncalibrated":
        raise SourceConfigurationChangedError("source_configuration_changed")

    connector_source = database_source_to_connector_config(source)
    digest_backfill = task.artifacts_summary.get("digest_backfill_window")
    if digest_backfill:
        if source["platform"] != "zsxq" or source["source_type"] != "topics":
            raise ValueError("digest_backfill_requires_zsxq_topics")
        connector_source["digest_backfill_window"] = digest_backfill
    from backend.application.workflows.zsxq_incremental import apply_incremental_window
    apply_incremental_window(base_path, source, connector_source, task)
    _ensure_personalized_feed_login(source, connector_source, capability=capability)
    cursor = None if research_fetch else SourceCursorRepository(base_path).get(source_id)
    cursor_before = None if initial_fetch or research_fetch else (cursor["cursor_value"] if cursor else None)
    connector = _build_connector(base_path, connector_source)
    try:
        fetch_result = connector.fetch_new_items(
            since_cursor=None if "--since" in connector_source.get("command", []) else cursor_before,
        )
    except Exception as error:
        from backend.application.workflows.acquisition_failures import (
            AcquisitionFailureError,
            classify_acquisition_failure,
        )

        failure = classify_acquisition_failure(source=connector_source, result=None, error=error)
        if failure is not None:
            raise AcquisitionFailureError(failure) from error
        raise
    raw_items = _annotate_raw_items(fetch_result.items, connector_source)
    if source["platform"] == "zsxq" and source["source_type"] == "topics":
        peers = SourceRepository(base_path).list_sources(include_disabled=False)
        if opportunity_source_for_topics(source, peers) is not None:
            raw_items = [item for item in raw_items if "风向标" not in item.raw_metadata.get("tags", [])]
    prefilter_evidence = {}
    if (source["platform"] in {"douyin", "xiaohongshu"} and source["source_type"] == "home_feed"
            and source["raw_metadata"].get("monetization_prefilter") is True):
        prefilter_evidence = connector.prefilter_evidence
        # The connector has already computed the snapshot and dedup counts from
        # all observations. Misses never reach durable RawItem/CARD_BUILD writes.
        raw_items = [item for item in raw_items if item.raw_metadata.get("prefilter", {}).get("status") != "missed"]
    douyin_cursor_diagnostic = None
    if (
        connector_source.get("source_platform") == "douyin"
        and connector_source.get("type") == "douyin_creator"
    ):
        if getattr(connector, "cursor_reinitialized", False):
            old_count = int(getattr(connector, "discovery_evidence", {}).get("old_cursor_id_count") or 0)
            douyin_cursor_diagnostic = {"old_cursor_id_count": old_count}
        raw_repo = RawItemRepository(base_path)
        existing_count = 0
        new_items = []
        for item in raw_items:
            if raw_repo.get_for_connector(item.connector_id, item.raw_item_id) is None:
                new_items.append(item)
            else:
                existing_count += 1
        raw_items = new_items
        fetch_result.dedup_skipped_count += existing_count
    next_cursor = cursor_before if digest_backfill else (
        None
        if research_fetch or (initial_fetch and not fetch_result.items)
        else fetch_result.next_cursor
    )
    run_id = str(task.artifacts_summary.get("run_id") or task.task_id)
    if isinstance(task.artifacts_summary.get("acquisition_checkpoint"), dict):
        run_id = f"{run_id}_recovery_{task.attempt}"
    chain_id = str(task.artifacts_summary.get("chain_id") or source_run_chain_id(source_id, _task_time(task)))
    chain_root_task_id = str(task.artifacts_summary.get("chain_root_task_id") or task.task_id)
    now = utc_now_seconds_z()
    next_run_at = None if research_fetch or digest_backfill else _next_run_at(source, finished_at=now)
    durable_task = task.claimed_by is not None
    discovery = _connector_discovery(connector)
    supplemental_failure = {}
    if error := getattr(connector, "digest_recheck_error", None):
        from backend.application.workflows.acquisition_failures import classify_acquisition_failure
        from backend.domain.failures.taxonomy import FetchFailureReason
        failure = classify_acquisition_failure(source=connector_source, result=None, error=error)
        if failure and failure.reason in {FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
                                         FetchFailureReason.PLATFORM_CHALLENGE_REQUIRED,
                                         FetchFailureReason.CHROME_NOT_CONFIGURED}:
            supplemental_failure["digest_recheck_failure"] = {
                "reason": failure.reason.value, "message": failure.message, "retryable": failure.retryable,
                "user_action": failure.user_action, "diagnostic": failure.diagnostic}

    run_payload = _run_payload(
        source=source,
        task=task,
        run_id=run_id,
        connector_id=str(connector_source["connector_id"]),
        now=now,
        next_run_at=next_run_at,
        cursor_before=cursor_before,
        cursor_after=next_cursor,
        fetched_count=len(raw_items),
        visible_count=int(fetch_result.visible_count),
        dedup_skipped_count=int(fetch_result.dedup_skipped_count),
        acquisition=fetch_result.acquisition,
    )
    if douyin_cursor_diagnostic is not None:
        run_payload["raw_payload"]["douyin_cursor_reinitialized"] = douyin_cursor_diagnostic
    run_payload["raw_payload"].update(prefilter_evidence)

    committed = commit_fetch_result(
        base_path,
        source=connector_source,
        run_id=run_id,
        raw_items=raw_items,
        next_cursor=next_cursor,
        chain_id=chain_id,
        chain_root_task_id=chain_root_task_id,
        task_id=task.task_id if durable_task else None,
        worker_id=task.claimed_by,
        task_attempt=task.attempt,
        checkpoint_context={
            **({"prefilter_summary": prefilter_evidence["prefilter_summary"]} if prefilter_evidence else {}),
            **acquisition_artifacts(fetch_result.acquisition),
            "cursor_before": cursor_before,
            "visible_count": int(fetch_result.visible_count),
            "fetched_count": len(raw_items),
            "dedup_skipped_count": int(fetch_result.dedup_skipped_count),
            **_connector_coverage(connector),
            **discovery,
            **supplemental_failure,
            **({"douyin_cursor_reinitialized": douyin_cursor_diagnostic} if douyin_cursor_diagnostic is not None else {}),
        },
        expected_source_version=expected_version,
        initial_fetch=initial_fetch,
        run_payload=run_payload,
        committed_last_run_at=now,
        committed_next_run_at=next_run_at,
        research_fetch=bool(research_fetch or digest_backfill),
    )
    return committed.checkpoint


def _run_payload(
    *, source: dict[str, Any], task: Task, run_id: str, connector_id: str,
    now: str, next_run_at: str | None, cursor_before: str | None, cursor_after: str | None,
    fetched_count: int, visible_count: int, dedup_skipped_count: int,
    acquisition: dict | None = None,
) -> dict[str, Any]:
    evidence = acquisition_artifacts(acquisition)
    return {
            "run_id": run_id,
            "source_id": str(source["source_id"]),
            "connector_id": connector_id,
            "scheduled_for": str(task.artifacts_summary.get("scheduled_for") or task.next_run_at or now),
            "started_at": now,
            "finished_at": now,
            "status": "success",
            "fetched_count": fetched_count,
            "visible_count": visible_count,
            "new_count": fetched_count,
            "skipped_count": 0,
            "dedup_skipped_count": dedup_skipped_count,
            "error_count": 0,
            "duration_ms": 0,
            "next_run_at": next_run_at,
            "attempt": task.attempt,
            "trigger": "task",
            "decision": None,
            "error_summary": (evidence.get("stop_reason") or "incomplete_batch")
            if evidence.get("completeness_status") == "partial" else None,
            "raw_payload": {
                **evidence,
                "cursor_before": cursor_before,
                "cursor_after": cursor_after,
                **_browser_repair_payload(),
            },
            "task_type": "fetch",
            "target_kind": task.target_kind,
            "target_id": task.target_id,
        }


def _admitted_source_version(base_path: Path, source: dict[str, Any], task: Task) -> str:
    current = source_fetch_input_version(source)
    declared = str(task.artifacts_summary.get("source_input_version") or task.input_version)
    if declared != LEGACY_INPUT_VERSION:
        if declared != current:
            raise SourceConfigurationChangedError("source_configuration_changed")
        return declared
    if task.claimed_by is None:
        return current
    from backend.infrastructure.storage.database import connect_writable
    from backend.infrastructure.storage.paths import resolve_sqlite_path
    conn = connect_writable(resolve_sqlite_path(base_path))
    try:
        conn.execute("begin immediate")
        updated = task_storage.update_admitted_source_version(
            conn, task_id=task.task_id, worker_id=task.claimed_by,
            attempt=task.attempt, version=current,
        )
        if not updated:
            raise SourceConfigurationChangedError("source_configuration_changed")
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()
    return current


class SourceAdmissionDeniedError(ValueError):
    """The current source is not admitted to ordinary acquisition."""


def _ensure_personalized_feed_login(
    source: dict[str, Any],
    connector_source: dict[str, Any],
    *,
    capability: Any,
) -> None:
    """Fail closed before a personalized feed can be calibrated or refreshed."""
    if (
        source["source_type"] != "home_feed"
        or capability.requires_login != REQUIRED_LOGIN
    ):
        return
    from backend.application.setup.state import AccountStatus
    from backend.application.setup.verify import probe_platform
    from backend.application.workflows.acquisition_failures import (
        AcquisitionFailureError,
        classify_acquisition_failure,
    )

    platform = source["platform"]
    account = probe_platform(platform)
    if account.status == AccountStatus.VERIFIED:
        return
    detail = str(account.error or account.status.value)
    error = RuntimeError(
        f"auth_required: {platform} personalized home feed login is not verified ({detail})"
    )
    failure = classify_acquisition_failure(
        source=connector_source,
        result=None,
        error=error,
    )
    if failure is None:
        raise error
    raise AcquisitionFailureError(failure)


def _task_time(task: Task) -> str:
    return str(task.artifacts_summary.get("scheduled_for") or task.next_run_at or task.task_id)




def _next_run_at(source: dict[str, Any], *, finished_at: str) -> str:
    if is_douyin_home_feed(source):
        return format_utc_seconds_z(next_session_at(parse_utc(finished_at), source.get("raw_metadata"), rng=Random()))
    cadence_hours = source["cadence_hours"]
    if cadence_hours == 0.25:
        cadence = "every_15_minutes"
    elif cadence_hours == 0.5:
        cadence = "every_30_minutes"
    elif cadence_hours == 1:
        cadence = "hourly"
    elif cadence_hours == 24:
        cadence = "daily"
    else:
        cadence = f"every_{int(cadence_hours)}_hours"
    return compute_next_run_at(cadence=cadence, last_finished_at=finished_at)


def _browser_repair_payload() -> dict[str, Any]:
    """Attach browser repair history to the eventual durable successful run."""
    from backend.application.workflows.browser_acquisition_batch import active_browser_acquisition_batch

    batch = active_browser_acquisition_batch()
    if batch is None or not batch.repair_attempts:
        return {}
    return {"repair_attempts": list(batch.repair_attempts)}


def _connector_discovery(connector: Any) -> dict[str, Any]:
    evidence = getattr(connector, "discovery_evidence", None)
    if not isinstance(evidence, dict) or evidence.get("availability") != "available":
        return {
            "discovered_link_count": None,
            "discovered_links": [],
            "discovery_availability": "unavailable",
            "discovery_evidence": {"availability": "unavailable"},
        }
    count = evidence.get("discovered_link_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return {
            "discovered_link_count": None,
            "discovered_links": [],
            "discovery_availability": "unavailable",
            "discovery_evidence": {"availability": "unavailable"},
        }
    links = [safe for value in (evidence.get("links") or [])[:50] if (safe := _safe_discovery_url(value))]
    arguments = [
        str(redact_secrets(str(value)))[:160]
        for value in (evidence.get("sanitized_arguments") or [])[:30]
    ]
    return {
        "discovered_link_count": count,
        "discovered_links": links,
        "discovery_availability": "available",
        "discovery_evidence": {
            "executor": str(redact_secrets(str(evidence.get("executor") or "")))[:80],
            "session_alias": str(redact_secrets(str(evidence.get("session_alias") or "")))[:80],
            "sanitized_arguments": arguments,
            "availability": "available",
            **({"digest_recheck": evidence["digest_recheck"]} if "digest_recheck" in evidence else {}),
            **({"pinned_item_count": evidence["pinned_item_count"]} if "pinned_item_count" in evidence else {}),
        },
    }


def _connector_coverage(connector: Any) -> dict[str, Any]:
    evidence = getattr(connector, "coverage_evidence", None)
    if not isinstance(evidence, dict):
        return {}
    return {"research_coverage": evidence}


def _safe_discovery_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:500]
