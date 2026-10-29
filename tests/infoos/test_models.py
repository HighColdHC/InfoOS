import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps
from backend.domain.models.normalized_item import NormalizedItem
from backend.domain.models.intel_card import CardTrace, CardRouting, CardScores, IntelCard
from backend.application.pipeline.normalize import normalize_raw_item


def test_raw_item_requires_minimum_fields_and_serializes():
    item = RawItem(
        raw_item_id="raw_001",
        connector_id="rss_test",
        source_platform="rss",
        source_type="article",
        source_url="https://example.com/post-1",
        captured_at="2026-04-18T16:00:00Z",
        raw_text="hello world",
        raw_author=RawAuthor(name="Alice"),
        raw_timestamps=RawTimestamps(published_at="2026-04-18T15:00:00Z"),
    )

    payload = item.model_dump()

    assert payload["raw_item_id"] == "raw_001"
    assert payload["raw_author"]["name"] == "Alice"
    assert payload["raw_timestamps"]["published_at"] == "2026-04-18T15:00:00Z"


def test_raw_item_rejects_missing_required_fields():
    with pytest.raises(ValidationError):
        RawItem(
            connector_id="rss_test",
            source_platform="rss",
            source_type="article",
            source_url="https://example.com/post-1",
            captured_at="2026-04-18T16:00:00Z",
        )


def test_normalized_item_keeps_cleaned_content_and_metadata():
    item = NormalizedItem(
        raw_item_id="raw_001",
        source_url="https://example.com/post-1",
        title="Post title",
        normalized_content="Clean body",
        summary="Short summary",
        published_at="2026-04-18T15:00:00Z",
        source_author="Alice",
        content_length=10,
        topics=["ai", "agents"],
    )

    assert item.normalized_content == "Clean body"
    assert item.topics == ["ai", "agents"]


def test_normalization_marks_title_only_content_as_insufficient():
    raw = RawItem(
        raw_item_id="raw_title_only",
        connector_id="x_list_ai",
        source_platform="x",
        source_type="list",
        source_url="https://x.com/example/status/1",
        captured_at="2026-05-11T00:00:00Z",
        raw_text="Same text",
        raw_author=RawAuthor(name="Example", handle="example", url=None),
        raw_timestamps=RawTimestamps(published_at="2026-05-11T00:00:00Z"),
        raw_metadata={"title": "Same text"},
    )

    normalized = normalize_raw_item(raw)

    assert normalized.raw_metadata["content_quality"] == "title_only"
    assert normalized.raw_metadata["content_needs_enrichment"] is True


def test_intel_card_requires_trace_without_declared_legacy_fields():
    card = IntelCard(
        card_id="card_001",
        source_platform="rss",
        source_type="article",
        source_url="https://example.com/post-1",
        captured_at="2026-04-18T16:00:00Z",
        processed_at="2026-04-18T16:01:00Z",
        title="Post title",
        normalized_content="Clean body",
        summary="Short summary",
        workflow_status="export_ready",
        trace=CardTrace(raw_item_id="raw_001"),
    )

    assert card.trace.raw_item_id == "raw_001"
    assert not {"topics", "scores", "recommended_action", "routing"} & IntelCard.model_fields.keys()


def test_intel_card_accepts_scoring_and_routing_metadata():
    card = IntelCard(
        card_id="card_001",
        source_platform="rss",
        source_type="article",
        source_url="https://example.com/post-1",
        captured_at="2026-04-18T16:00:00Z",
        processed_at="2026-04-18T16:01:00Z",
        title="Post title",
        normalized_content="Clean body",
        summary="Short summary",
        scores=CardScores(timeliness_score=3, insight_score=5, creation_score=2, relevance_score=4),
        recommended_action="topic_pool",
        routing=CardRouting(target="topic_pool", reason="high insight makes this worth tracking in the topic pool"),
        workflow_status="routed",
        trace=CardTrace(raw_item_id="raw_001"),
    )

    assert card.scores.insight_score == 5
    assert card.recommended_action == "topic_pool"
    assert card.routing.target == "topic_pool"


def test_intel_card_rejects_missing_trace():
    with pytest.raises(ValidationError):
        IntelCard(
            card_id="card_001",
            source_platform="rss",
            source_type="article",
            source_url="https://example.com/post-1",
            captured_at="2026-04-18T16:00:00Z",
            processed_at="2026-04-18T16:01:00Z",
            title="Post title",
            normalized_content="Clean body",
            summary="Short summary",
            workflow_status="export_ready",
        )
