"""Parse historical run JSON once, before typed projections consume it."""
from __future__ import annotations

import json
from typing import TypedDict, cast
from collections.abc import Iterable

from backend.domain.sources.acquisition import AcquisitionArtifacts, acquisition_artifacts

from backend.domain.json_types import JsonObject


class DiscoveryEvidence(TypedDict):
    availability: object
    executor: object
    session_alias: object
    sanitized_arguments: object


class RunCheckpoint(TypedDict):
    available: bool
    acquisition: object
    card_build_task_ids: Iterable[object]
    raw_item_keys: Iterable[object]
    raw_item_ids: Iterable[object]
    connector_id: object
    connector_key: str
    source_id: object
    provider: object
    operation: object
    initial_fetch: bool
    cursor_before: object
    cursor_after: object
    fetched_count: object
    visible_count: object
    dedup_skipped_count: object
    discovered_link_count: object
    discovery_dedup_count: object
    raw_item_count: object
    discovery_availability: str
    discovery_evidence: DiscoveryEvidence
    discovered_links: Iterable[object]


class DiscoveryCounts(TypedDict):
    total: int | None
    dedup: int | None
    pending: int | None


class AssetArtifact(TypedDict):
    index: int
    payload: JsonObject


class RunArtifacts(TypedDict):
    acquisition: object
    acquisition_checkpoint: object
    chain_id: object
    chain_key: str
    chain_root_task_id: object
    card_id: str | None
    trigger: object
    reason: object
    completeness_status: object
    created_or_updated: object
    version: object
    assets: Iterable[object]


def json_object(raw: object) -> JsonObject:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(cast(str, raw) or "")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def json_list(raw: object) -> list[object]:
    try:
        value = json.loads(cast(str, raw) or "")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(cast(str, value))
    except (TypeError, ValueError):
        return None


def parse_run_checkpoint(raw: object) -> RunCheckpoint:
    value = json_object(raw)
    evidence = value.get("discovery_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    return {
        "available": bool(value),
        "acquisition": value.get("acquisition"),
        "card_build_task_ids": cast(Iterable[object], value.get("card_build_task_ids") or []),
        "raw_item_keys": cast(Iterable[object], value.get("raw_item_keys") or []),
        "raw_item_ids": cast(Iterable[object], value.get("raw_item_ids") or []),
        "connector_id": value.get("connector_id"),
        "connector_key": str(value.get("connector_id") or ""),
        "source_id": value.get("source_id"),
        "provider": value.get("provider"),
        "operation": value.get("operation") or "fetch",
        "initial_fetch": value.get("initial_fetch") is True,
        "cursor_before": value.get("cursor_before"),
        "cursor_after": value.get("cursor_after"),
        "fetched_count": value.get("fetched_count") or 0,
        "visible_count": value.get("visible_count") or 0,
        "dedup_skipped_count": value.get("dedup_skipped_count") or 0,
        "discovered_link_count": value.get("discovered_link_count"),
        "discovery_dedup_count": value.get("dedup_skipped_count"),
        "raw_item_count": value.get("raw_item_count"),
        "discovery_availability": str(value.get("discovery_availability") or "unavailable"),
        "discovery_evidence": {
            "availability": evidence.get("availability"),
            "executor": evidence.get("executor"),
            "session_alias": evidence.get("session_alias"),
            "sanitized_arguments": evidence.get("sanitized_arguments") or [],
        },
        "discovered_links": cast(Iterable[object], value.get("discovered_links") or []),
    }


def parse_run_artifacts(raw: object) -> RunArtifacts:
    value = json_object(raw)
    return {
        "acquisition": value.get("acquisition"),
        "acquisition_checkpoint": value.get("acquisition_checkpoint") or {},
        "chain_id": value.get("chain_id"),
        "chain_key": str(value.get("chain_id") or ""),
        "chain_root_task_id": value.get("chain_root_task_id"),
        "card_id": str(value["card_id"]) if value.get("card_id") else None,
        "trigger": value.get("trigger"),
        "reason": value.get("reason"),
        "completeness_status": value.get("completeness_status"),
        "created_or_updated": value.get("created_or_updated"),
        "version": value.get("version", value.get("card_version")),
        "assets": cast(Iterable[object], value.get("assets") or []),
    }


def run_count(primary: int | None, fallback: object) -> int:
    """Coerce lazily: nonzero SQL counts mask even malformed historical JSON."""
    return int(cast(str, primary or fallback or 0))


def checkpoint_raw_keys(values: Iterable[object]) -> list[tuple[str, str]]:
    return [
        (str(item.get("connector_id")), str(item.get("raw_item_id")))
        for item in values
        if isinstance(item, dict) and item.get("connector_id") and item.get("raw_item_id")
    ]


def parse_run_assets(values: Iterable[object]) -> list[AssetArtifact]:
    # Keep original indices, and the historical iteration errors for non-arrays.
    return [{"index": index, "payload": item}
            for index, item in enumerate(values) if isinstance(item, dict)]


def fetch_acquisition(
    artifacts: RunArtifacts, checkpoint: RunCheckpoint | None = None,
) -> AcquisitionArtifacts:
    acquisition = artifacts["acquisition"]
    if not acquisition:
        acquisition = checkpoint["acquisition"] if checkpoint is not None and checkpoint["available"] else cast(
            JsonObject, artifacts["acquisition_checkpoint"]
        ).get("acquisition")
    return acquisition_artifacts(acquisition)


def parse_discovery_counts(checkpoint: RunCheckpoint) -> DiscoveryCounts:
    # Lists never used these counts. Preserve their coercion only in discovery.
    return {
        "total": _optional_int(checkpoint["discovered_link_count"]),
        "dedup": _optional_int(checkpoint["discovery_dedup_count"]),
        "pending": _optional_int(checkpoint["raw_item_count"]),
    }
