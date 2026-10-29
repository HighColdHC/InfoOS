import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.infoos._factories import cards_for_source
from backend.workers.scheduler.cycle import run_scheduler_cycle
from backend.application.tasks.types import TaskType
from backend.application.tasks.worker import run_due_tasks
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import SourceRepository


def test_v1_sqlite_acquisition_closed_loop(tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "AI Feed",
            "default_domain_id": None,
            "cadence_hours": 1,
            "notes": "AI feed",
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_ai",
        }
    )

    class FakeConnector:
        connector_id = "rss_ai"

        def fetch_new_items(self, since_cursor):
            return FetchResult(
                items=[
                    RawItem(
                        raw_item_id="raw_smoke",
                        connector_id="rss_ai",
                        source_platform="rss",
                        source_type="article",
                        source_url="https://example.com/card",
                        captured_at="2026-05-14T01:00:01Z",
                        raw_text="Closed loop content.",
                        raw_metadata={"title": "Closed loop card"},
                    )
                ],
                next_cursor="raw_smoke",
            )

    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_args: FakeConnector())
    monkeypatch.setattr("backend.application.workflows.acquisition.utc_now_seconds_z", lambda: "2026-05-14T01:00:02Z")

    result = run_scheduler_cycle(base_dir=tmp_path, max_attempts=2)
    fetch_result = run_due_tasks(tmp_path, task_types=[TaskType.FETCH], max_workers=1)
    card_build_result = run_due_tasks(tmp_path, task_types=[TaskType.CARD_BUILD], max_workers=1)

    source = SourceRepository(tmp_path).get_source("rss_ai")
    cards = cards_for_source(tmp_path, 'rss_ai')
    assert result["status"] == "success"
    assert result["sources"]["status"] == "planned"
    assert fetch_result["succeeded"] == 1
    assert card_build_result["succeeded"] == 1
    assert source["last_run_at"] == "2026-05-14T01:00:02Z"
    assert source["next_run_at"] == "2026-05-14T02:00:02Z"
    assert len(cards) == 1
    assert cards[0]["content"] == "Closed loop content."
