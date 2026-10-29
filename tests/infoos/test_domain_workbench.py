import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.infoos._legacy_history import seed_legacy_domain
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository, RunRepository, SourceRepository
from backend.app.main import create_app
from tests.infoos._factories import upsert_current_insight


def _seed_db_source_and_card(base_dir: Path) -> None:
    """Seed an RSS source and one card directly into SQLite.

    This replaces the legacy ``_seed_legacy_card_and_registry`` helper that
    wrote YAML config and JSON card files. The workbench is now DB-only.
    """
    domain_id = seed_legacy_domain(base_dir, "ai")
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "RSS AI",
            "default_domain_id": domain_id,
            "cadence_hours": 24.0,
            "notes": None,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_ai",
        }
    )
    CardRepository(base_dir).upsert_card(
        {
            "card_id": "card_legacy",
            "source_id": "rss_ai",
            "source_platform": "rss",
            "source_type": "article",
            "source_url": "https://example.com/legacy",
            "source_author": "Example",
            "title": "Legacy AI Card",
            "content": "Legacy full content from JSON card.",
            "summary": "Legacy AI summary",
            "primary_domain_id": domain_id,
            "published_at": "2026-05-08T01:00:00Z",
            "captured_at": "2026-05-08T01:01:00Z",
            "processed_at": "2026-05-08T01:02:00Z",
            "workflow_status": "routed",
            "raw_payload": {"topics": ["ai"], "trace": {"raw_item_id": "legacy"}},
        }
    )
    _seed_ready_human_card(base_dir, "card_legacy", "Legacy full content from JSON card.")


def _seed_ready_human_card(base_dir: Path, card_id: str, body: str) -> None:
    CardContentBlockRepository(base_dir).upsert_block(
        {
            "block_id": f"block_{card_id}_content",
            "card_id": card_id,
            "position": 0,
            "layer": "original",
            "block_kind": "linked_article",
            "status": "enriched",
            "body": body,
            "segments": [],
            "source_url": f"https://example.com/{card_id}",
            "provenance": "test fixture",
            "raw_payload": {},
        }
    )
    upsert_current_insight(
        base_dir,
        card_id,
        {
            "main_point": f"AI interpretation for {card_id}.",
            "topic_assignment": None,
            "scores": {"timeliness": 4, "insight": 4, "creation": 3, "relevance": 5},
            "score_reasons": {"timeliness": "Fixture event time is present."},
            "why_it_matters": "This card is ready for the human main list.",
            "is_time_sensitive": False,
            "evidence_quotes": [body[:80]],
            "status": "succeeded",
            "last_error": None,
            "model": "gpt-test",
            "prompt_version": "card_insight_v1",
            "generated_at": "2026-05-08T02:00:00Z",
        },
    )


def test_homepage_seeds_domain_workbench_from_legacy_files(tmp_path):
    _seed_db_source_and_card(tmp_path)
    client = TestClient(create_app(base_dir=tmp_path))

    sources = client.get("/api/configurable-sources")

    assert sources.json()["sources"][0]["source_id"] == "rss_ai"


def test_workbench_refreshes_source_registry_metadata_after_initial_seed(tmp_path):
    _seed_db_source_and_card(tmp_path)
    client = TestClient(create_app(base_dir=tmp_path))
    assert client.get("/api/configurable-sources").json()["sources"][0]["cadence_hours"] == 24.0

    # Update the source cadence directly in the DB.
    SourceRepository(tmp_path).update_source("rss_ai", cadence_hours=1.0)

    sources = client.get("/api/configurable-sources").json()["sources"]

    assert sources[0]["cadence_hours"] == 1.0


def test_configurable_source_center_and_run_page_are_visible(tmp_path):
    domain_id = seed_legacy_domain(tmp_path, "AI")
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "x_ai_list",
            "platform": "x",
            "source_type": "list",
            "identifier": "12345",
            "display_name": "X AI List",
            "default_domain_id": domain_id,
            "cadence_hours": 1,
            "notes": "AI creators",
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "x_ai_list_connector",
        }
    )
    RunRepository(tmp_path).append_run(
        {
            "run_id": "run_x_ai_list",
            "source_id": "x_ai_list",
            "connector_id": "x_ai_list_connector",
            "scheduled_for": "2026-05-08T01:00:00Z",
            "started_at": "2026-05-08T01:00:01Z",
            "finished_at": "2026-05-08T01:00:03Z",
            "status": "success",
            "fetched_count": 10,
            "new_count": 3,
            "skipped_count": 7,
            "error_count": 0,
            "duration_ms": 2000,
            "next_run_at": "2026-05-08T02:00:03Z",
            "attempt": 1,
            "trigger": "scheduled",
            "raw_payload": {},
        }
    )
    client = TestClient(create_app(base_dir=tmp_path))

    sources = client.get("/api/configurable-sources")

    assert "x_ai_list" in sources.text
