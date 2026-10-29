"""Preview or enqueue bounded article repairs through CONTENT_ENRICH."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.application.tasks.input_versions import InputSnapshot
from backend.application.tasks.planner import card_task_id
from backend.application.tasks.repository import TaskRepository
from backend.domain.tasks import Task, TaskStatus, TaskType
from backend.domain.zsxq_article import ARTICLE_POLICY_VERSION
from backend.infrastructure.storage.zsxq_article import ZsxqArticleRepository


def backfill_zsxq_articles(base_dir: Path | str, *, limit: int, apply: bool = False,
                          after_card_id: str = "") -> dict[str, Any]:
    if not 1 <= limit <= 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    candidates = ZsxqArticleRepository(base_dir).candidates(limit=limit, after_card_id=after_card_id)
    tasks = TaskRepository.for_base_dir(base_dir)
    enqueued: list[str] = []
    already_queued: list[str] = []
    if apply:
        for candidate in candidates:
            card_id = candidate["card_id"]
            version = InputSnapshot(
                target_kind="card", target_id=card_id, stage="content_enrich",
                payload={"policy": ARTICLE_POLICY_VERSION, "generation": candidate["read_generation"]},
            ).version()
            task_id = card_task_id(TaskType.CONTENT_ENRICH, card_id, input_version=version)
            active = any(task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
                         for task in tasks.list_by_target(target_kind="card", target_id=card_id,
                                                         task_type=TaskType.CONTENT_ENRICH))
            if active or tasks.get(task_id) is not None:
                already_queued.append(card_id)
                continue
            tasks.enqueue(Task(task_id=task_id, task_type=TaskType.CONTENT_ENRICH,
                               target_kind="card", target_id=card_id, input_version=version,
                               artifacts_summary={"card_id": card_id, "zsxq_article_backfill": True,
                                                  "queue_platform": "zsxq", "chain_id": task_id}))
            enqueued.append(task_id)
    return {"dry_run": not apply, "count": len(candidates), "preview": candidates[:10],
            "enqueued": enqueued, "already_queued": already_queued,
            "after_card_id": candidates[-1]["card_id"] if candidates else after_card_id}


def main() -> int:
    parser = argparse.ArgumentParser(description="预览或入队知识星球文章回填，不直接访问浏览器")
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--after-card-id", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(backfill_zsxq_articles(args.base_dir, limit=args.limit, apply=args.apply,
                                          after_card_id=args.after_card_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
