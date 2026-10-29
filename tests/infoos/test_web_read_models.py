import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.read_models.source_management import read_source_detail
from backend.read_models.source_management_page import _current_calibrations
from backend.infrastructure.storage.database import connect_readonly, default_database_path
from backend.read_models.task_manager import read_task_list
from backend.application.tasks.planner import enqueue_source_calibration_task
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.infrastructure.storage.repositories import (
    CardContentBlockRepository,
    CardRepository,
    RunRepository,
    SourceRepository,
)


def _card_payload(card_id: str, *, title: str, published_at: str, source_platform: str = "rss") -> dict:
    return {
        "card_id": card_id,
        "title": title,
        "summary": f"{title} summary",
        "source_url": f"https://example.com/{card_id}",
        "source_platform": source_platform,
        "source_type": "article",
        "source_id": f"source_{source_platform}",
        "captured_at": published_at,
        "processed_at": published_at,
        "published_at": published_at,
        "normalized_content": f"{title} body with agent context",
        "topics": ["ai", source_platform],
        "workflow_status": "scored",
        "scores": {
            "timeliness_score": 3,
            "insight_score": 4,
            "creation_score": 3,
            "relevance_score": 4,
        },
        "trace": {"raw_item_id": f"raw-{card_id}"},
        "recommended_action": "topic_pool",
        "routing": {"target": "topic_pool", "reason": "track"},
    }


def _seed_source(base_dir: Path, source_id: str = "source_rss", *, topics: list[str] | None = None) -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": source_id,
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "RSS Source",
            "default_domain_id": None,
            "cadence_hours": 24.0,
            "notes": "test source",
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_test",
            "raw_metadata": {
                "source_lane": "core",
                "topics": topics or ["ai"],
                "purpose": "test source",
                "review_policy": "auto_collect",
            },
        }
    )


def _persist_card(base_dir: Path, payload: dict) -> None:
    if payload.get("source_id"):
        _seed_source(base_dir, payload["source_id"], topics=payload.get("topics") or ["ai"])
    raw_payload = payload.get("raw_payload") or payload
    CardRepository(base_dir).upsert_card(
        {
            "card_id": payload["card_id"],
            "source_id": payload.get("source_id"),
            "source_platform": payload["source_platform"],
            "source_type": payload["source_type"],
            "source_url": payload["source_url"],
            "source_author": payload.get("source_author"),
            "title": payload["title"],
            "content": payload.get("normalized_content") or payload.get("content") or "",
            "summary": payload.get("summary") or "",
            "published_at": payload.get("published_at"),
            "captured_at": payload["captured_at"],
            "processed_at": payload["processed_at"],
            "workflow_status": payload.get("workflow_status"),
            "raw_payload": raw_payload,
        }
    )


def _append_run(base_dir: Path, run_id: str, status: str, *, source_id: str = "source_rss", finished_at: str = "2026-04-30T08:00:02Z") -> None:
    RunRepository(base_dir).append_run(
        {
            "run_id": run_id,
            "source_id": source_id,
            "connector_id": "rss_test",
            "scheduled_for": finished_at,
            "started_at": finished_at,
            "finished_at": finished_at,
            "status": status,
            "fetched_count": 1 if status == "success" else 0,
            "new_count": 1 if status == "success" else 0,
            "skipped_count": 0,
            "error_count": 0 if status == "success" else 1,
            "duration_ms": 1000,
            "next_run_at": "2026-05-01T08:00:02Z",
            "attempt": 1,
            "trigger": "scheduled",
            "decision": None,
            "error_summary": None if status == "success" else "network failed",
            "raw_payload": {},
        }
    )


def test_source_detail_counts_cards_without_treating_blocked_enrichment_as_degraded(tmp_path):
    cards_dir = tmp_path / "data" / "cards"
    config_dir = tmp_path / "config"
    cards_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    blocked = _card_payload("card_blocked", title="Blocked Card", published_at="2026-04-30T08:00:00Z")
    _persist_card(tmp_path, blocked)
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_card_blocked_comments",
            "card_id": "card_blocked",
            "position": 0,
            "layer": "context",
            "block_kind": "platform_comments",
            "status": "blocked",
            "body": "",
            "segments": [],
            "source_url": "https://example.com/post",
            "provenance": "test",
            "raw_payload": {"reason": "platform_comments_unavailable"},
        }
    )

    detail = read_source_detail(tmp_path, "source_rss")

    assert detail["source"]["readiness_status"] == "ready"
    assert detail["source"]["generated_card_count"] == 1


def test_source_health_exposes_durable_calibration_lifecycle(tmp_path):
    source = {
        "source_id": "rss_calibrating",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/calibrating.xml",
        "display_name": "RSS Calibrating",
        "cadence_hours": 24,
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": "rss_calibrating",
    }
    SourceRepository(tmp_path).upsert_source(source)
    task_id = enqueue_source_calibration_task(tmp_path, source, now="2026-07-11T08:00:00Z")

    with connect_readonly(default_database_path(tmp_path)) as conn:
        current = _current_calibrations(conn, [source])[source["source_id"]]
    assert current.task_id == task_id
    assert current.status == "pending"
    assert read_source_detail(tmp_path, source["source_id"])["source"]["readiness_status"] == "awaiting_initial_fetch"


def test_source_health_prefers_current_calibration_version_over_stale_pending_task(tmp_path):
    source = {
        "source_id": "rss_versioned_health",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/old.xml",
        "display_name": "Versioned health",
        "cadence_hours": 24,
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": "rss_versioned_health",
    }
    SourceRepository(tmp_path).upsert_source(source)
    old_task_id = enqueue_source_calibration_task(tmp_path, source, now="2026-07-11T08:00:00Z")
    changed = {**source, "identifier": "https://example.com/current.xml"}
    SourceRepository(tmp_path).upsert_source(changed)
    current_task_id = enqueue_source_calibration_task(tmp_path, changed, now="2026-07-11T08:01:00Z")

    with connect_readonly(default_database_path(tmp_path)) as conn:
        current = _current_calibrations(conn, [changed])[source["source_id"]]
    assert old_task_id != current_task_id
    assert current.task_id == current_task_id


def test_task_list_preserves_calibration_stage_and_fetch_link(tmp_path):
    source = {
        "source_id": "rss_chain",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/chain.xml",
        "display_name": "RSS Chain",
        "cadence_hours": 24,
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": "rss_chain",
    }
    SourceRepository(tmp_path).upsert_source(source)
    calibration_id = enqueue_source_calibration_task(tmp_path, source, now="2026-07-11T08:00:00Z")
    calibration = TaskRepository.for_base_dir(tmp_path).get(calibration_id)
    TaskRepository.for_base_dir(tmp_path).enqueue(
        calibration.__class__(
            task_id="fetch_rss_chain_20260711_080000",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="rss_chain",
            status=TaskStatus.PENDING,
            depends_on=(),
            next_run_at="2026-07-11T08:00:00Z",
            artifacts_summary={
                "chain_id": calibration.artifacts_summary["chain_id"],
                "chain_root_task_id": calibration_id,
                "upstream_task_id": calibration_id,
                "scheduled_for": "2026-07-11T08:00:00Z",
            },
        )
    )

    rows = read_task_list(tmp_path, source_id="rss_chain", now="2026-07-11T08:00:00Z")
    projected = {row["task_id"]: row for row in rows}

    assert projected[calibration_id]["display_stage"] == "source_calibrate_history"
    fetch = projected["fetch_rss_chain_20260711_080000"]
    assert fetch["display_stage"] == "fetch"
    assert fetch["upstream_task_id"] == calibration_id
    assert fetch["chain_id"] == projected[calibration_id]["chain_id"]
