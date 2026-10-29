import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.infrastructure.storage.repositories import CardRepository
from backend.app.main import create_app
from tests.infoos._factories import upsert_current_insight


def test_card_api_rejects_historical_llm_rerun(tmp_path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_1",
            "source_id": None,
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/example/status/1",
            "source_author": "example",
            "title": "Agent update",
            "content": "Detailed agent update.",
            "summary": "Agent update.",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-05-08T01:00:00Z",
            "captured_at": "2026-05-08T01:01:00Z",
            "processed_at": "2026-05-08T01:02:00Z",
            "raw_payload": {},
        }
    )
    upsert_current_insight(
        tmp_path,
        "card_1",
        {
            "main_point": "Agent update matters.",
            "topic_assignment": None,
            "scores": {"timeliness": 4, "insight": 4, "creation": 3, "relevance": 5},
            "score_reasons": {"timeliness": "Fresh"},
            "why_it_matters": "It matters now.",
            "is_time_sensitive": True,
            "evidence_quotes": ["agent update"],
            "status": "succeeded",
            "last_error": None,
            "model": "gpt-test",
            "prompt_version": "card_insight_v1",
            "generated_at": "2026-05-08T01:10:00Z",
        },
    )
    client = TestClient(create_app(base_dir=tmp_path))

    rerun = client.post("/api/cards/card_1/llm-rerun")

    assert rerun.status_code == 404
