"""Passive service, collection coverage and provider status for the Web shell."""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict, cast

from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.runtime.container_health import read_container_health
from backend.infrastructure.storage.database import connect_readonly, default_database_path


class ProviderStatus(TypedDict):
    provider_id: str
    state: str
    recovery_mode: str
    reason_code: str | None
    user_action: str | None
    cooldown_until: str | None


class QueueCountRow(TypedDict):
    status: str
    n: int


class SourceCounts(TypedDict):
    total: int
    enabled: int


class CollectorStatus(TypedDict):
    service_status: str
    latest_collection_at: str | None
    collection_status: str
    enabled_sources: int
    total_sources: int
    overdue_sources: int
    queue: dict[str, int]
    providers: list[ProviderStatus]
    runtime_status: str
    reasons: list[str]


def read_collector_status(base_dir: Path) -> CollectorStatus:
    with connect_readonly(default_database_path(base_dir)) as conn:
        latest: str | None = conn.execute("select max(finished_at) from run_history where task_type = 'fetch' and status = 'success'").fetchone()[0]
        sources = cast(SourceCounts, dict(conn.execute("select count(*) total, coalesce(sum(enabled = 1),0) enabled from sources where source_scope = 'maintained' and coalesce(json_extract(raw_metadata,'$.system_managed'),0)=0").fetchone()))
        providers = [cast(ProviderStatus, dict(row)) for row in conn.execute("select scope_key provider_id,state,recovery_mode,reason_code,user_action,cooldown_until from runtime_circuits where scope_kind = 'provider' and state <> 'closed'")]
        queue_rows = [
            cast(QueueCountRow, dict(row))
            for row in conn.execute("select status,count(*) n from tasks where status in ('pending','running') group by status")
        ]
        queue = {row["status"]: row["n"] for row in queue_rows}
        overdue: int = conn.execute("""select count(*) from sources where source_scope = 'maintained' and coalesce(json_extract(raw_metadata,'$.system_managed'),0)=0 and enabled = 1
            and cadence_hours > 0 and julianday(next_run_at) < julianday('now','-30 minutes')""").fetchone()[0]
    health = read_container_health()
    reasons = []
    if health['status'] == 'unhealthy':
        reasons.append('runtime_unhealthy')
    if providers:
        reasons += [p['reason_code'] or 'provider_unavailable' for p in providers]
    if overdue:
        reasons.append('sources_overdue')
    status = 'degraded' if reasons else 'paused' if sources['enabled'] == 0 else 'unknown' if health['status'] == 'unknown' else 'ready'
    return {
        'service_status':status, 'latest_collection_at':normalize_timestamp(latest),
        'collection_status':'paused' if sources['enabled'] == 0 else 'active',
        'enabled_sources':sources['enabled'], 'total_sources':sources['total'],
        'overdue_sources':overdue, 'queue':queue, 'providers':providers,
        'runtime_status':health['status'], 'reasons':sorted(set(reasons)),
    }


__all__ = ['read_collector_status']
