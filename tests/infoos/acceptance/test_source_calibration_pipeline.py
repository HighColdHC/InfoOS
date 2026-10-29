"""Acceptance coverage from source creation through information-card finalize."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.infoos._factories import cards_for_source
from backend.app.main import create_app
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import RawItemRepository, SourceCalibrationRepository
from backend.read_models.task_manager import read_task_list


class CalibrationPipelineConnector:
    connector_id = "calibration_acceptance_connector"

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        return FetchResult(
            items=[
                RawItem(
                    raw_item_id="calibration-remote-1",
                    connector_id=self.connector_id,
                    source_platform="rss",
                    source_type="article",
                    source_url="https://example.com/calibration-remote-1",
                    captured_at="2026-07-11T00:00:00Z",
                    raw_text="Durable calibration makes newly added sources enter the intelligence pipeline.",
                    raw_metadata={"title": "Calibration queue acceptance"},
                )
            ],
            next_cursor="calibration-cursor-1",
            visible_count=1,
        )


def _run_until_idle(base_dir: Path) -> None:
    for _ in range(24):
        result = run_due_tasks(base_dir, max_workers=1, limit=40)
        if result["processed"] == 0:
            return
    raise AssertionError("source calibration acceptance queue did not become idle")


def test_added_source_initial_fetch_reaches_information_card_and_task_list(tmp_path, monkeypatch):
    from backend.application.enrichment import generic_blocks
    from backend.application.workflows import acquisition

    connector = CalibrationPipelineConnector()
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: connector)
    monkeypatch.setattr(generic_blocks, "enrich_generic_card_blocks", lambda *_args: [])

    response = TestClient(create_app(tmp_path)).post(
        "/api/configurable-sources",
        json={
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/calibration-acceptance.xml",
            "display_name": "Calibration acceptance RSS",
            "cadence_hours": 24,
            "enabled": True,
        },
    )
    assert response.status_code == 201
    submitted = response.json()
    source_id = str(submitted["source"]["source_id"])
    initial_task_id = str(submitted["initial_fetch_task_id"])
    assert submitted["calibration_task_id"] == initial_task_id
    assert submitted["queue_status"] == "calibration_pending"

    _run_until_idle(tmp_path)

    repo = TaskRepository.for_base_dir(tmp_path)
    initial = repo.get(initial_task_id)
    assert initial is not None and initial.status is TaskStatus.SUCCESS
    assert initial.artifacts_summary["initial_fetch"] is True
    assert SourceCalibrationRepository(tmp_path).get_by_task_id(initial_task_id)["result"] == "success"
    fetches = repo.list_by_target(target_kind="source", target_id=source_id, task_type=TaskType.FETCH)
    assert len(fetches) == 1 and fetches[0].status is TaskStatus.SUCCESS
    assert len(RawItemRepository(tmp_path).list_raw_items()) == 1
    cards = cards_for_source(tmp_path, source_id)
    assert len(cards) == 1
    assert cards[0]["status"] == "active"
    assert cards[0]["version"] == 1
    assert str(cards[0]["content_hash"]).startswith("sha256:")

    rows = read_task_list(tmp_path, source_id=source_id)
    stages = [row["display_stage"] for row in rows]
    assert stages.count("initial_fetch") == 1
    assert "source_calibrate" not in stages
    assert "card_build" in stages
    assert "card_finalize" in stages
    assert "output_generate" not in stages
