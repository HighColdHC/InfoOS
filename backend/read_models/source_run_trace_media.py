"""Current Card and persisted ContentBlock media projection for Source Run."""
from __future__ import annotations

from backend.domain.card_titles import display_title

from pathlib import Path
from typing import NotRequired, TypedDict
from collections.abc import Mapping, Sequence

from backend.infrastructure.storage.source_run_json import RunCheckpoint, json_list, json_object, parse_discovery_counts, parse_run_assets
from backend.infrastructure.storage.source_run_card_progress_queries import CardProgressGraph, CardProgressTaskRow
from backend.infrastructure.storage.source_run_trace_queries_types import RawItemRow, TaskRow, TraceAssetRow, TraceCardRow, TracePage, PipelineRows
from urllib.parse import quote

from backend.infrastructure.storage.paths import resolve_data_dir
from backend.read_models.human_cards import is_user_facing_content_block
from backend.read_models.source_run_trace_safety import (
    redact_text, safe_or_opaque_identifier, safe_label, sanitize_external_url, sanitize_value,
    source_identifier_fields, task_identifier_alias,
)


class DiscoveryRequest(TypedDict):
    executor: str
    session_alias: str
    sanitized_arguments: object
    fetch_task_id: str
    links: list[str]
    availability: str


class Discovery(TypedDict):
    total_links: int | None
    dedup_skipped: int | None
    pending_items: int | None
    availability: str
    evidence: DiscoveryRequest


class CardBuild(TypedDict):
    total: int
    complete: int | None
    partial: int | None
    failed: int | None
    retry_count: int
    availability: NotRequired[str]


CardBuildRows = PipelineRows | CardProgressGraph

class TaskEvidence(TypedDict):
    task_id: str
    status: str
    error_summary: str | None
    retryable: bool


class BranchCounts(TypedDict):
    total: int | None
    success: int | None
    failed: int | None
    retry_count: int | None
    tasks: list[TaskEvidence]
    availability: NotRequired[str]


class FinalizeBranch(TypedDict):
    status: str
    version: object
    completeness: str
    task_id: str
    availability: str


class CaptureRawItem(TypedDict):
    id: str
    source_url: None


class CaptureCard(TypedDict):
    id: str
    title: str


class ItemCapture(TypedDict):
    raw_item: CaptureRawItem
    card: CaptureCard
    title: str
    image_download: BranchCounts
    video_download: BranchCounts
    video_transcription: BranchCounts
    finalize: FinalizeBranch


class MediaItem(TypedDict):
    block_id: str
    position: int
    block_kind: str
    order_semantics: str
    media_src: str | None
    source_link: str | None
    mime_type: str
    status: str


class FinalBlock(TypedDict):
    block_id: str
    position: int
    block_kind: str
    status: str
    source_link: str | None
    media_src: NotRequired[str | None]
    body: NotRequired[str]
    segments: NotRequired[object]


class Monitoring(TypedDict):
    platform: str
    source_type: str
    display_name: str
    cadence_hours: float | str
    identifier_display: str
    identifier_url: str


class CardObject(TypedDict):
    source_platform: str
    source_type: str
    source_author: str


class SourceContext(TypedDict):
    config_semantics: str
    config_availability: str
    monitoring: Monitoring
    card_object_semantics: str
    card_object: CardObject


class FinalCard(TypedDict):
    projection_semantics: str
    provenance: str
    card_id: str
    title: str
    content: str
    summary: str
    current_projection_url: str
    blocks: list[FinalBlock]
    media: list[MediaItem]
    source_context: SourceContext

_TEXT_LIMIT = 4000
_SEGMENT_LIMIT = 40


def project_discovery(checkpoint: RunCheckpoint, fetch_task_id: str) -> Discovery:
    """Project only explicitly persisted discovery counts and safe request evidence."""

    counts = parse_discovery_counts(checkpoint)
    total, dedup, pending = counts["total"], counts["dedup"], counts["pending"]
    declared = checkpoint["discovery_availability"]
    availability = "available" if declared == "available" and None not in (total, dedup, pending) else "unavailable"
    evidence = checkpoint["discovery_evidence"]
    links = []
    for value in checkpoint["discovered_links"]:
        safe = sanitize_external_url(value)
        if safe:
            links.append(safe)
    evidence_availability = (
        "available"
        if availability == "available" and str(evidence["availability"]) == "available"
        else "unavailable"
    )
    return {
        "total_links": total if availability == "available" else None,
        "dedup_skipped": dedup if availability == "available" else None,
        "pending_items": pending if availability == "available" else None,
        "availability": availability,
        "evidence": {
            "executor": safe_label(evidence["executor"]) if evidence_availability == "available" else "unavailable",
            "session_alias": safe_label(evidence["session_alias"]) if evidence_availability == "available" else "unavailable",
            "sanitized_arguments": sanitize_value(evidence["sanitized_arguments"])
            if evidence_availability == "available" else [],
            "fetch_task_id": task_identifier_alias(fetch_task_id),
            "links": links if evidence_availability == "available" else [],
            "availability": evidence_availability,
        },
    }


def project_card_build(
    checkpoint: RunCheckpoint,
    pipeline_rows: CardBuildRows,
    events: Sequence[Mapping[str, object]],
) -> CardBuild:
    """Summarize card outcomes from checkpoint roots and their finalize tasks."""

    roots = [str(value) for value in checkpoint["card_build_task_ids"] if value]
    by_root: dict[str, dict[str, CardProgressTaskRow]] = {}
    for raw in pipeline_rows["graph"]:
        root_id = raw["root_id"]
        task_id = raw["task_id"]
        if root_id in roots:
            by_root.setdefault(root_id, {})[task_id] = raw
    complete = partial = failed = 0
    unavailable = bool(pipeline_rows["page_truncated"])
    for root_id in roots:
        tasks = list(by_root.get(root_id, {}).values())
        root = next((task for task in tasks if task["task_id"] == root_id and task["task_type"] == "card_build"), None)
        if root is None:
            unavailable = True
            continue
        finalizes = [task for task in tasks if task["task_type"] == "card_finalize"]
        successful = [task for task in finalizes if task["status"] == "success"]
        if successful:
            finalize = max(successful, key=lambda task: (task["attempt"], task["task_id"]))
            completeness = finalize["artifacts"]["completeness_status"]
            if completeness == "complete":
                complete += 1
            elif completeness == "partial":
                partial += 1
            else:
                unavailable = True
        elif root["status"] in {"failed", "blocked"} or any(
            task["status"] in {"failed", "blocked"} for task in finalizes
        ):
            failed += 1
        else:
            unavailable = True
    target_types = {
        "card_build", "content_enrich", "media_download",
        "video_extract_audio", "video_transcribe", "card_finalize",
    }
    associated = {
        task["task_id"]: task
        for tasks in by_root.values()
        for task in tasks.values()
        if task["task_type"] in target_types
    }
    result: CardBuild = {
        "total": len(roots),
        "complete": complete,
        "partial": partial,
        "failed": failed,
        "retry_count": _retry_count(list(associated.values()), events),
    }
    if unavailable:
        result.update({"complete": None, "partial": None, "failed": None, "availability": "unavailable"})
    return result


def project_item_capture(
    loaded: TracePage,
    *,
    raw: RawItemRow | None,
    card: TraceCardRow | None,
    tasks: Sequence[TaskRow],
) -> ItemCapture:
    """Build one note's independent persisted media and finalize tree."""

    card_id = card["card_id"] if card else ""
    title = display_title(card["source_platform"], card["title"], card["content"]) if card else display_title(None, "", None)
    assets = [row for row in loaded["assets"] if row["card_id"] == card_id]
    events = loaded["events"]
    media_tasks = [task for task in tasks if task["task_type"] == "media_download"]
    transcript_tasks = [task for task in tasks if task["task_type"] == "video_transcribe"]
    return {
        "raw_item": {
            "id": safe_or_opaque_identifier("raw", raw["raw_item_id"] if raw else None),
            "source_url": None,
        },
        "card": {
            "id": safe_or_opaque_identifier("card", card_id),
            "title": redact_text(title, limit=240) or "unavailable",
        },
        "title": redact_text(title, limit=240) or "unavailable",
        "image_download": _download_branch("image", assets, media_tasks, events),
        "video_download": _download_branch("video", assets, media_tasks, events),
        "video_transcription": _transcription_branch(assets, transcript_tasks, events),
        "finalize": _finalize_branch(tasks),
    }


def project_media(base_dir: Path | str, loaded: TracePage, card_id: str | None) -> list[MediaItem]:
    if not card_id:
        return []
    assets = {row["asset_id"]: row for row in loaded["assets"] if row["card_id"] == card_id}
    result: list[MediaItem] = []
    used_assets: set[str] = set()
    for block in (row for row in loaded["blocks"] if row["card_id"] == card_id):
        if block["layer"] != "media" or block["block_kind"] not in {"image", "video"}:
            continue
        raw_payload = json_object(block["raw_payload"])
        asset = assets.get(str(raw_payload.get("asset_id") or ""))
        if asset is None:
            matches = [row for row in assets.values() if row["local_path"] and row["local_path"] == block["body"]]
            asset = matches[0] if len(matches) == 1 else None
        if asset is None or asset["asset_id"] in used_assets:
            continue
        used_assets.add(asset["asset_id"])
        result.append({
            "block_id": safe_or_opaque_identifier("block", block["block_id"]), "position": block["position"], "block_kind": block["block_kind"],
            "order_semantics": "persisted ContentBlock order / current retained collection",
            "media_src": _asset_url(base_dir, asset["local_path"]),
            "source_link": sanitize_external_url(asset["remote_url"] or asset["source_url"]),
            "mime_type": safe_label(asset["mime_type"]), "status": safe_label(asset["status"]),
        })
    return result


def project_final_card(
    base_dir: Path | str, loaded: TracePage, card: TraceCardRow | None, provenance: str
) -> FinalCard | None:
    if card is None:
        return None
    card_id = card["card_id"]
    media = project_media(base_dir, loaded, card_id)
    media_by_block = {row["block_id"]: row for row in media}
    blocks: list[FinalBlock] = []
    for row in loaded["blocks"]:
        if row["card_id"] != card_id or not is_user_facing_content_block(row):
            continue
        block: FinalBlock = {"block_id": safe_or_opaque_identifier("block", row["block_id"]), "position": row["position"],
                 "block_kind": row["block_kind"], "status": safe_label(row["status"]),
                 "source_link": sanitize_external_url(row["source_url"])}
        if row["block_kind"] in {"image", "video"}:
            projected = media_by_block.get(safe_or_opaque_identifier("block", row["block_id"]))
            if projected is None:
                continue
            block["media_src"] = projected["media_src"]
        else:
            block["body"] = redact_text(row["body"], limit=_TEXT_LIMIT)
            block["segments"] = sanitize_value(json_list(row["segments"])[:_SEGMENT_LIMIT])
        blocks.append(block)
    source_context = _source_context(loaded, card)
    return {
        "projection_semantics": "当前 Card 投影，非运行时快照", "provenance": provenance,
        "card_id": safe_or_opaque_identifier("card", card_id), "title": redact_text(display_title(card["source_platform"], card["title"], card["content"]), limit=_TEXT_LIMIT),
        "content": redact_text(card["content"], limit=_TEXT_LIMIT),
        "summary": redact_text(card["summary"], limit=_TEXT_LIMIT),
        "current_projection_url": sanitize_external_url(card["source_url"]) or "unavailable",
        "blocks": blocks, "media": media, "source_context": source_context,
    }


def _source_context(loaded: TracePage, card: TraceCardRow) -> SourceContext:
    available = bool(loaded["platform"] and loaded["source_type"] and loaded["display_name"])
    identifier_display, identifier_url = source_identifier_fields(
        loaded["identifier"], platform=loaded["platform"], source_type=loaded["source_type"]
    ) if available else (
        "unavailable", "unavailable"
    )
    monitoring: Monitoring = {
        "platform": safe_label(loaded["platform"]) if available else "unavailable",
        "source_type": safe_label(loaded["source_type"]) if available else "unavailable",
        "display_name": redact_text(loaded["display_name"], limit=240) if available else "unavailable",
        "cadence_hours": loaded["cadence_hours"] if available and loaded["cadence_hours"] is not None else "unavailable",
        "identifier_display": identifier_display,
        "identifier_url": identifier_url,
    }
    return {
        "config_semantics": "current_source_config_not_runtime_snapshot",
        "config_availability": "available" if available else "unavailable",
        "monitoring": monitoring,
        "card_object_semantics": "current_card_projection",
        "card_object": {
            "source_platform": safe_label(card["source_platform"]),
            "source_type": safe_label(card["source_type"]),
            "source_author": redact_text(card["source_author"], limit=240) or "unavailable",
        },
    }


def _asset_url(base_dir: Path | str, local_path: str | None) -> str | None:
    if not local_path or Path(local_path).is_absolute() or ".." in Path(local_path).parts:
        return None
    root = (resolve_data_dir(base_dir) / "assets").resolve()
    target = (root / local_path).resolve()
    try:
        relative = target.relative_to(root)
    except ValueError:
        return None
    return "/api/assets/" + quote(relative.as_posix(), safe="/")


def _download_branch(
    kind: str,
    assets: list[TraceAssetRow],
    tasks: Sequence[TaskRow],
    events: Sequence[Mapping[str, object]],
) -> BranchCounts:
    selected = [asset for asset in assets if asset["asset_kind"] == kind]
    relevant_tasks = [
        task for task in tasks
        if any(
            asset["payload"].get("asset_kind") == kind
            for asset in parse_run_assets(task["artifacts"]["assets"])
        )
    ]
    if not selected and not relevant_tasks:
        return _unavailable_counts()
    return {
        "total": len(selected),
        "success": sum(asset["status"] in {"downloaded", "understood"} for asset in selected),
        "failed": sum(asset["status"] in {"failed", "blocked", "skipped"} for asset in selected),
        "retry_count": _retry_count(relevant_tasks, events),
        "tasks": [_task_evidence(task) for task in relevant_tasks],
    }


def _transcription_branch(
    assets: list[TraceAssetRow],
    tasks: Sequence[TaskRow],
    events: Sequence[Mapping[str, object]],
) -> BranchCounts:
    video_ids = {asset["asset_id"] for asset in assets if asset["asset_kind"] == "video"}
    selected = [task for task in tasks if task["target_id"] in video_ids]
    if not selected:
        return _unavailable_counts()
    return {
        "total": len(selected),
        "success": sum(task["status"] == "success" for task in selected),
        "failed": sum(task["status"] in {"failed", "blocked"} for task in selected),
        "retry_count": _retry_count(selected, events),
        "tasks": [_task_evidence(task) for task in selected],
    }


def _finalize_branch(tasks: Sequence[TaskRow]) -> FinalizeBranch:
    candidates = [task for task in tasks if task["task_type"] == "card_finalize"]
    if not candidates:
        return {
            "status": "unavailable", "version": None, "completeness": "unavailable",
            "task_id": "unavailable", "availability": "unavailable",
        }
    task = max(candidates, key=lambda item: (item["attempt"], item["task_id"]))
    artifacts = task["artifacts"]
    return {
        "status": safe_label(task["status"]),
        "version": artifacts["version"],
        "completeness": safe_label(artifacts["completeness_status"]),
        "task_id": task_identifier_alias(task["task_id"]),
        "availability": "available",
    }


def _retry_count(tasks: Sequence[CardProgressTaskRow], events: Sequence[Mapping[str, object]]) -> int:
    result = 0
    for task in tasks:
        task_id = task["task_id"]
        durable_attempts = max(0, task["attempt"] - 1)
        event_attempts = sum(
            event["task_id"] == task_id and event["event_type"] == "retry_scheduled"
            for event in events
        )
        result += max(durable_attempts, event_attempts)
    return result


def _unavailable_counts() -> BranchCounts:
    return {
        "total": None, "success": None, "failed": None,
        "retry_count": None, "tasks": [], "availability": "unavailable",
    }


def _task_evidence(task: TaskRow) -> TaskEvidence:
    status = task["status"]
    return {
        "task_id": task_identifier_alias(task["task_id"]),
        "status": status,
        "error_summary": redact_text(task["error_reason"], limit=240) or None,
        "retryable": (
            status in {"failed", "blocked"}
            and task["attempt"] < task["max_attempts"]
        ),
    }
