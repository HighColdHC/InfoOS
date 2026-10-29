"""Bounded, persisted facts for the source maintenance list."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

from backend.domain.calibration.matrix import REQUIRED_LOGIN
from backend.domain.sources.capability import resolve_source_capability
from backend.domain.sources.configuration import database_source_to_connector_config
from backend.domain.sources.registry import source_browser_platforms
from backend.domain.sources.versioning import source_fetch_input_version
from backend.infrastructure.browser import session_state
from backend.infrastructure.runtime.environment import setup_state_path
from backend.infrastructure.runtime.setup_state import load_state
from backend.infrastructure.storage.database import connect_readonly, default_database_path
from backend.infrastructure.storage.sources import SourceRow
from backend.read_models.source_facts import (
    LatestRunRow,
    RecentRunCounts,
    load_source_card_facts,
    load_source_run_facts,
)
from backend.read_models.source_health import (
    CalibrationTask,
    SourceReadiness,
    _derived_readiness_status,
)


class SourceVersionRow(TypedDict):
    source_id: str
    config_version: str
    created_at: str


class SourcePageFacts(NamedTuple):
    readiness: dict[str, SourceReadiness]
    latest_runs: dict[str, LatestRunRow]
    card_counts: dict[str, int]
    login_by_platform: dict[str, str]
    recent_run_counts: dict[str, RecentRunCounts]


def load_source_page_facts(
    base_dir: Path,
    sources: list[SourceRow],
    *, source_ids: list[str] | None = None,
) -> SourcePageFacts:
    """Read only fields used by list filters, summary and current page items."""
    setup = load_state(setup_state_path())
    login_by_platform = {
        platform: account.status.value
        for platform, account in setup.accounts.items()
        if platform in source_browser_platforms()
    }
    browser_configured = session_state.is_alive(session_state.read())
    with connect_readonly(default_database_path(base_dir)) as conn:
        latest_runs, recent_run_counts = load_source_run_facts(conn, source_ids)
        card_counts, degraded_sources = load_source_card_facts(conn, source_ids)
        calibrations = _current_calibrations(conn, sources)

    readiness_by_source: dict[str, SourceReadiness] = {}
    for source in sources:
        source_id = source["source_id"]
        readiness = _persisted_readiness(
            source, login_by_platform, browser_configured=browser_configured,
        )
        readiness["status"] = _derived_readiness_status(
            source,
            calibrations.get(source_id),
            metadata_fallback=source_id in degraded_sources,
            base_readiness=readiness,
        )
        readiness_by_source[source_id] = readiness
    return SourcePageFacts(
        readiness_by_source, latest_runs, card_counts, login_by_platform, recent_run_counts,
    )


def load_latest_source_versions(base_dir: Path, source_ids: list[str]) -> dict[str, SourceVersionRow]:
    if not source_ids:
        return {}
    placeholders = ", ".join("?" for _ in source_ids)
    with connect_readonly(default_database_path(base_dir)) as conn:
        rows = conn.execute(
            f"""select source_id, config_version, created_at from (
                  select source_id, config_version, created_at,
                         row_number() over (
                           partition by source_id order by created_at desc, rowid desc
                         ) as rank
                  from source_config_versions where source_id in ({placeholders})
                ) where rank = 1""",
            source_ids,
        ).fetchall()
    return {row["source_id"]: cast(SourceVersionRow, dict(row)) for row in rows}


def _current_calibrations(
    conn: sqlite3.Connection, sources: list[SourceRow],
) -> dict[str, CalibrationTask]:
    if not sources:
        return {}
    versions = {source["source_id"]: source_fetch_input_version(source) for source in sources}
    values = ", ".join("(?, ?)" for _ in versions)
    params = [part for pair in versions.items() for part in pair]
    rows = conn.execute(
        f"""with current(source_id, input_version) as (values {values})
            select t.target_id, t.task_id, t.task_type, t.status, t.attempt
            from current cross join tasks t indexed by idx_tasks_target
            where t.target_kind = 'source'
              and t.target_id = current.source_id
              and t.input_version = current.input_version
              and (t.task_type = 'source_calibrate' or (
                t.task_type = 'fetch' and
                json_type(case when json_valid(t.artifacts_summary)
                               then t.artifacts_summary else '{{}}' end,
                          '$.initial_fetch') = 'true'
              ))
            order by t.target_id, t.next_run_at, t.created_at, t.task_id""",
        params,
    ).fetchall()
    initial: dict[str, CalibrationTask] = {}
    calibration: dict[str, CalibrationTask] = {}
    exact: dict[str, CalibrationTask] = {}
    for row in rows:
        source_id = row["target_id"]
        task = CalibrationTask(row["task_id"], row["status"], row["attempt"])
        if row["task_type"] == "fetch":
            initial.setdefault(source_id, task)
        else:
            calibration.setdefault(source_id, task)
            expected_id = f"source_calibrate_{source_id}_{versions[source_id][:12]}"
            if task.task_id == expected_id:
                exact[source_id] = task
    return {
        source_id: selected
        for source_id in versions
        if (selected := initial.get(source_id) or exact.get(source_id) or calibration.get(source_id))
    }


def _persisted_readiness(
    source: SourceRow, login_by_platform: dict[str, str], *, browser_configured: bool,
) -> SourceReadiness:
    """Use setup and session snapshots; the list must never inspect Chrome cookies."""
    platform = source["platform"]
    capability = resolve_source_capability(source)
    connector = database_source_to_connector_config(source)
    if not source["enabled"]:
        return {"status": "disabled", "issues": ["来源已停用"], "diagnostic": None}
    if capability.status == "implemented_uncalibrated":
        return {"status": "blocked", "issues": ["当前配置尚待完成首次采集"],
                "diagnostic": {"category": "configuration"}}
    if capability.status in {"planned", "unsupported"}:
        message = "该来源能力尚在规划中" if capability.status == "planned" else "该来源能力暂不支持"
        return {"status": "blocked", "issues": [message],
                "diagnostic": {"category": "configuration"}}
    if connector.get("type") == "rss":
        issues: list[str] = [] if source["identifier"] else ["RSS 来源需要订阅地址"]
        return {"status": "ready" if not issues else "blocked", "issues": issues,
                "diagnostic": {"category": "configuration"} if issues else None}
    if platform in source_browser_platforms():
        requires_session = connector.get("type") == "opencli"
        issues = []
        if requires_session and not browser_configured:
            issues.append("当前实例未找到有效的专用浏览器会话记录")
        if browser_configured and capability.requires_login == REQUIRED_LOGIN and login_by_platform.get(platform) != "verified":
            issues.append("尚未确认该平台在专用浏览器中的登录状态")
        category = "auth_required" if browser_configured and capability.requires_login == REQUIRED_LOGIN and login_by_platform.get(platform) != "verified" else "browser_session"
        return {"status": "ready" if not issues else "blocked", "issues": issues,
                "diagnostic": {"category": category} if issues else None}
    if platform in {"hackernews", "producthunt"}:
        return {"status": "ready", "issues": [], "diagnostic": None}
    return {"status": "blocked", "issues": ["暂不支持该平台"],
            "diagnostic": {"category": "configuration"}}
