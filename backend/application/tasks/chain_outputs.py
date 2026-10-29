"""Per-acquisition-chain output evidence for release and live acceptance gates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.domain.outputs.validation import output_validation_errors
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.tasks import operations as task_storage


def acquired_chain_output_evidence(base_dir: Path | str, chain_id: str) -> dict[str, Any]:
    """Return output-or-ignore evidence for every card built by one chain.

    An output from another acquired chain must never make this chain complete.
    The durable output task is therefore the join boundary: it must have the
    chain id and target the card produced by this chain.
    """
    if not chain_id:
        return _empty_evidence()
    with connect_existing(resolve_sqlite_path(base_dir)) as conn:
        task_rows = task_storage.chain_task_rows(conn)
        artifact_rows = conn.execute(
            """
            select artifact_id, artifact_type, source_decision_id, body
            from output_artifacts
            where status != 'archived'
            """
        ).fetchall()
    chain_tasks = [
        dict(row) | {"artifacts": _json_object(row["artifacts_summary"])}
        for row in task_rows
        if _json_object(row["artifacts_summary"]).get("chain_id") == chain_id
    ]
    artifacts = {str(row["artifact_id"]): dict(row) for row in artifact_rows}
    card_ids = sorted({
        str(task["artifacts"].get("card_id"))
        for task in chain_tasks
        if task["task_type"] == "card_build"
        and task["status"] == "success"
        and task["artifacts"].get("card_id")
    })
    unresolved_card_build_task_ids = sorted(
        str(task["task_id"])
        for task in chain_tasks
        if task["task_type"] == "card_build"
        and task["status"] == "success"
        and not task["artifacts"].get("card_id")
    )
    output_tasks = [task for task in chain_tasks if task["task_type"] == "output_generate"]
    valid_cards: list[str] = []
    ignored_cards: list[str] = []
    missing_cards: list[str] = []
    invalid_cards: list[str] = []
    for card_id in card_ids:
        card_tasks = [task for task in output_tasks if task["target_id"] == card_id]
        if any(_is_explicit_ignore(task) for task in card_tasks):
            ignored_cards.append(card_id)
        elif any(_has_valid_output(task, artifacts) for task in card_tasks):
            valid_cards.append(card_id)
        elif card_tasks:
            (missing_cards if any(_output_artifact_is_missing(task) for task in card_tasks) else invalid_cards).append(card_id)
        else:
            missing_cards.append(card_id)
    return {
        "card_ids": card_ids,
        "valid_output_card_ids": valid_cards,
        "explicit_ignore_card_ids": ignored_cards,
        "missing_output_card_ids": missing_cards,
        "invalid_output_card_ids": invalid_cards,
        "unresolved_card_build_task_ids": unresolved_card_build_task_ids,
        "valid_outputs": len(valid_cards),
        "explicit_ignores": len(ignored_cards),
    }


def _has_valid_output(task: dict[str, Any], artifacts: dict[str, dict[str, Any]]) -> bool:
    if task["status"] != "success":
        return False
    summary = task["artifacts"]
    artifact_id = str(summary.get("artifact_id") or "")
    decision_id = str(summary.get("decision_id") or "")
    artifact = artifacts.get(artifact_id)
    return bool(
        artifact
        and decision_id
        and artifact.get("source_decision_id") == decision_id
        and not output_validation_errors(str(artifact.get("artifact_type") or ""), _json_object(artifact.get("body")))
    )


def _is_explicit_ignore(task: dict[str, Any]) -> bool:
    summary = task["artifacts"]
    return bool(
        task["status"] == "success"
        and summary.get("explicit_no_output") is True
        and summary.get("route") == "ignore"
    )


def _output_artifact_is_missing(task: dict[str, Any]) -> bool:
    return bool(task["status"] == "success" and not task["artifacts"].get("artifact_id"))


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _empty_evidence() -> dict[str, Any]:
    return {
        "card_ids": [],
        "valid_output_card_ids": [],
        "explicit_ignore_card_ids": [],
        "missing_output_card_ids": [],
        "invalid_output_card_ids": [],
        "unresolved_card_build_task_ids": [],
        "valid_outputs": 0,
        "explicit_ignores": 0,
    }
