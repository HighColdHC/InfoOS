"""Preview or apply bounded cleanup of one exact X media content hash."""

from __future__ import annotations

from backend.domain.timestamps import format_utc_seconds_z

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any

from backend.application.tasks.input_versions import card_finalize_input_version
from backend.application.tasks.planner import card_task_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.infrastructure.storage.database import default_database_path, ensure_database_initialized
from backend.infrastructure.storage.paths import resolve_data_dir
from backend.infrastructure.media.thumbnails import thumbnail_relative_path
from backend.infrastructure.storage.x_media_cleanup import XMediaCleanupStore


_CONTENT_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path("."))
    parser.add_argument("--content-hash", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    content_hash = str(args.content_hash).strip().lower()
    if not _CONTENT_HASH.fullmatch(content_hash):
        parser.error("--content-hash must be an exact sha256:<64 lowercase hex> value")

    ensure_database_initialized(default_database_path(args.base_dir))
    repo = XMediaCleanupStore(args.base_dir)
    plan = repo.plan(content_hash)
    if not args.apply:
        print(json.dumps({"mode": "preview", **plan}, ensure_ascii=False, indent=2))
        return
    result = apply_cleanup(args.base_dir, content_hash, plan=plan)
    print(json.dumps({"mode": "apply", **result}, ensure_ascii=False, indent=2))


def apply_cleanup(
    base_dir: Path | str,
    content_hash: str,
    *,
    plan: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not _CONTENT_HASH.fullmatch(content_hash):
        raise ValueError("content_hash must be an exact sha256:<64 lowercase hex> value")
    base_path = Path(base_dir)
    repo = XMediaCleanupStore(base_path)
    frozen_plan = plan or repo.plan(content_hash)
    if frozen_plan.get("content_hash") != content_hash:
        raise ValueError("cleanup plan content_hash does not match requested hash")
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    occurred_at = format_utc_seconds_z(timestamp)
    quarantine_dir = resolve_data_dir(base_path) / "quarantine" / "x_media_cleanup" / timestamp.strftime("%Y%m%dT%H%M%SZ")
    quarantine_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = quarantine_dir / "manifest.json"
    moves: list[dict[str, Any]] = []
    manifest = {
        "operation": "x_media_cleanup",
        "status": "prepared",
        "created_at": occurred_at,
        "content_hash": content_hash,
        "deleted_rows": {
            "card_assets": [asset for card in frozen_plan["affected_cards"] for asset in card["assets"]],
            "card_content_blocks": [
                block
                for card in frozen_plan["affected_cards"]
                for block in [*card["image_blocks"], *card["linked_image_blocks"]]
            ],
        },
        "cards_before": [card["card"] for card in frozen_plan["affected_cards"]],
        "file_moves": moves,
    }
    task_ids: list[str] = []
    try:
        _move_assets(resolve_data_dir(base_path), quarantine_dir, frozen_plan, moves=moves)
        _write_manifest(manifest_path, manifest)
        with repo.transaction() as conn:
            current_plan = repo.plan(content_hash, connection=conn)
            if _plan_identity(current_plan) != _plan_identity(frozen_plan):
                raise RuntimeError("cleanup target changed after preview; rerun preview")
            mutations = repo.delete_and_increment(conn, current_plan, updated_at=occurred_at)
            tasks: list[Task] = []
            for item in current_plan["affected_cards"]:
                card, blocks, assets = repo.finalize_state(conn, str(item["card_id"]))
                input_version = card_finalize_input_version(card, blocks, assets)
                task = Task(
                    task_id=card_task_id(TaskType.CARD_FINALIZE, str(item["card_id"]), input_version=input_version),
                    task_type=TaskType.CARD_FINALIZE,
                    target_kind="card",
                    target_id=str(item["card_id"]),
                    input_version=input_version,
                    artifacts_summary={
                        "card_id": item["card_id"],
                        "maintenance_reason": "x_media_cleanup",
                        "content_hash": content_hash,
                    },
                )
                tasks.append(task)
            TaskRepository.for_base_dir(base_path).enqueue_many(tasks, conn=conn)
            task_ids = [task.task_id for task in tasks]
    except Exception:
        _restore_moves(moves)
        manifest["status"] = "rolled_back"
        try:
            _write_manifest(manifest_path, manifest)
        except OSError:
            pass
        raise

    assets_root = resolve_data_dir(base_path) / "assets"
    for card in frozen_plan["affected_cards"]:
        for asset in card["assets"]:
            (assets_root / thumbnail_relative_path(str(asset["asset_id"]))).unlink(missing_ok=True)

    manifest.update(status="applied", applied_at=occurred_at, task_ids=task_ids, mutations=mutations)
    _write_manifest(manifest_path, manifest)
    return {
        "content_hash": content_hash,
        "affected_card_count": frozen_plan["affected_card_count"],
        **mutations,
        "task_ids": task_ids,
        "quarantine_dir": str(quarantine_dir),
        "manifest_path": str(manifest_path),
    }


def _move_assets(
    data_dir: Path,
    quarantine_dir: Path,
    plan: dict[str, Any],
    *,
    moves: list[dict[str, Any]],
) -> None:
    seen: set[Path] = set()
    data_root = data_dir.resolve()
    for card in plan["affected_cards"]:
        for asset in card["assets"]:
            local_path = str(asset.get("local_path") or "").strip()
            if not local_path:
                raise ValueError(f"asset {asset['asset_id']} has no local_path; nothing can be quarantined")
            source = Path(local_path)
            source = source if source.is_absolute() else data_dir / "assets" / source
            source = source.resolve()
            try:
                relative = source.relative_to(data_root)
            except ValueError:
                raise ValueError(f"asset {asset['asset_id']} is outside the data directory") from None
            if source in seen:
                moves.append({"asset_id": asset["asset_id"], "source": str(source), "status": "shared_file_already_moved"})
                continue
            seen.add(source)
            destination = quarantine_dir / relative
            if not source.is_file():
                raise FileNotFoundError(f"asset {asset['asset_id']} file is missing: {source}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moves.append(
                {
                    "asset_id": asset["asset_id"],
                    "source": str(source),
                    "destination": str(destination),
                    "status": "moved",
                }
            )
def _restore_moves(moves: list[dict[str, Any]]) -> None:
    for move in reversed(moves):
        if move.get("status") != "moved":
            continue
        source = Path(str(move["source"]))
        destination = Path(str(move["destination"]))
        if destination.is_file() and not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source))


def _plan_identity(plan: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    asset_ids = tuple(sorted(str(asset["asset_id"]) for card in plan["affected_cards"] for asset in card["assets"]))
    block_ids = tuple(
        sorted(
            str(block["block_id"])
            for card in plan["affected_cards"]
            for block in [*card["image_blocks"], *card["linked_image_blocks"]]
        )
    )
    return asset_ids, block_ids


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    main()
