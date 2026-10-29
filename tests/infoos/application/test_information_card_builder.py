from __future__ import annotations

from backend.application.pipeline.information_card_builder import build_information_card
from backend.application.pipeline.normalize import normalize_raw_item
from backend.application.tasks.handlers.acquisition import handle_card_build
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.models.raw_item import RawAuthor, RawItem
from backend.infrastructure.storage.repositories import CardRepository, RawItemRepository, SourceRepository


def _raw_item(*, source_id: str = "rss_ai", run_id: str = "run_a") -> RawItem:
    return RawItem(
        raw_item_id="post-42",
        connector_id="rss_ai",
        source_platform="rss",
        source_type="article",
        source_url="https://example.com/posts/42",
        captured_at="2026-07-16T01:02:03Z",
        raw_text="A durable article body.",
        raw_author=RawAuthor(name="Example Author"),
        raw_metadata={
            "source_id": source_id,
            "title": "  Durable   article  ",
            "run_id": run_id,
            "topics": ["must-not-be-routed"],
        },
    )


def test_information_card_builder_keeps_identity_stable_and_omits_downstream_semantics() -> None:
    first_raw = _raw_item(run_id="run_a")
    second_raw = _raw_item(run_id="run_b")

    first = build_information_card(normalize_raw_item(first_raw), first_raw)
    second = build_information_card(normalize_raw_item(second_raw), second_raw)

    assert first.card_id == second.card_id
    assert first.title == "Durable article"
    assert first.card_type == "information"
    assert first.workflow_status == "processing"
    assert not {"topics", "scores", "recommended_action", "routing", "primary_domain_id",
                "secondary_domain_ids"} & first.model_dump().keys()


def test_information_card_builder_preserves_x_source_scope_for_the_same_url() -> None:
    first_raw = _raw_item(source_id="x_home_for_you").model_copy(
        update={"source_platform": "x", "connector_id": "x_home_for_you"}
    )
    second_raw = first_raw.model_copy(
        update={
            "connector_id": "x_user_example",
            "raw_metadata": {**first_raw.raw_metadata, "source_id": "x_user_example"},
        }
    )

    first = build_information_card(normalize_raw_item(first_raw), first_raw)
    second = build_information_card(normalize_raw_item(second_raw), second_raw)

    assert first.card_id != second.card_id


def test_information_card_builder_reuses_x_identity_across_research_batches() -> None:
    first_raw = RawItem(
            raw_item_id="123",
            connector_id="research_x_batch_a",
            source_platform="x",
            source_type="social_post",
            source_url="https://x.com/openai/status/123",
            captured_at="2026-07-31T00:00:00Z",
            raw_text="Research item",
            raw_metadata={"source_id": "research_a_x", "source_scope": "research"},
    )
    first = build_information_card(normalize_raw_item(first_raw), first_raw)
    second_raw = first_raw.model_copy(
        update={
            "connector_id": "research_x_batch_b",
            "raw_metadata": {"source_id": "research_b_x", "source_scope": "research"},
        }
    )
    second = build_information_card(
        normalize_raw_item(second_raw),
        second_raw,
    )

    assert first.card_id == second.card_id


def test_information_card_builder_reuses_research_identity_without_a_url() -> None:
    first_raw = RawItem(
        raw_item_id="stable-provider-id",
        connector_id="research:batch-a:reddit",
        source_platform="reddit",
        source_type="social_post",
        source_url="",
        captured_at="2026-07-31T00:00:00Z",
        raw_text="Research item",
        raw_metadata={"source_id": "research_a_reddit", "source_scope": "research"},
    )
    second_raw = first_raw.model_copy(
        update={
            "connector_id": "research:batch-b:reddit",
            "raw_metadata": {"source_id": "research_b_reddit", "source_scope": "research"},
        }
    )

    first = build_information_card(normalize_raw_item(first_raw), first_raw)
    second = build_information_card(normalize_raw_item(second_raw), second_raw)

    assert first.card_id == second.card_id


def test_information_card_builder_ignores_research_tracking_query_changes() -> None:
    first_raw = RawItem(
        raw_item_id="note-42",
        connector_id="research:batch-a:xiaohongshu",
        source_platform="xiaohongshu",
        source_type="social_post",
        source_url="https://www.xiaohongshu.com/explore/note-42?xsec_token=first",
        captured_at="2026-07-31T00:00:00Z",
        raw_text="Research item",
        raw_metadata={"source_id": "research_a_xhs", "source_scope": "research"},
    )
    second_raw = first_raw.model_copy(
        update={
            "connector_id": "research:batch-b:xiaohongshu",
            "source_url": "https://www.xiaohongshu.com/explore/note-42?xsec_token=second",
            "raw_metadata": {"source_id": "research_b_xhs", "source_scope": "research"},
        }
    )

    first = build_information_card(normalize_raw_item(first_raw), first_raw)
    second = build_information_card(normalize_raw_item(second_raw), second_raw)

    assert first.card_id == second.card_id


def test_card_build_handler_persists_the_unfinalized_information_card_contract(tmp_path) -> None:
    raw_item = _raw_item()
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Example feed",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_ai",
        }
    )
    RawItemRepository(tmp_path).upsert_raw_items([raw_item], run_id="source_run_7")

    result = handle_card_build(
        Task(
            task_id="card_build_post_42",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id=raw_item.raw_item_id,
        ),
        tmp_path,
    )

    assert result["status"] == TaskStatus.SUCCESS.value
    stored = CardRepository(tmp_path).get_card(result["artifacts"]["card_id"])
    assert stored["status"] == "processing"
    assert stored["completeness_status"] == "processing"
    assert stored["processor_version"] == "information-card-builder-v1"
    assert stored["raw_connector_id"] == "rss_ai"
    assert stored["raw_item_id"] == "post-42"
    assert stored["source_run_id"] == "source_run_7"
    assert stored["primary_domain_id"] is None
    assert stored["secondary_domain_ids"] == []
    assert not {"scores", "routing", "normalized_content", "summary"} & stored["raw_payload"].keys()
    assert stored["raw_payload"]["raw_payload"]["content_quality"] == "thin"


def test_card_payload_whitelist_preserves_legacy_read_projections(tmp_path):
    from backend.application.pipeline.information_card_builder import information_card_payload
    from backend.domain.advertising import advertising_projection
    from backend.domain.card_payload import (
        CARD_COUNTER_FIELDS, CARD_METADATA_CONTAINERS, CARD_METADATA_FIELDS, compact_card_metadata,
    )
    from backend.domain.engagement_metrics import extract_engagement_metrics
    from backend.domain.sources.zsxq_digest import card_is_digest

    metadata = {key: 3 for key in CARD_METADATA_FIELDS | CARD_COUNTER_FIELDS}
    metadata.update(is_digest=True, is_ad=True, content_quality="thin", content_needs_enrichment=True,
                    content_source="feishu", content_source_status="partial", feed_content_origin="atom_content")
    for container in CARD_METADATA_CONTAINERS:
        metadata[container] = {"is_ad": True, "ad_id": "ad-1", "likes": 7, "text": "duplicate body"}
    metadata["external_doc_read"].update(content_source="feishu", content_source_status="partial")
    metadata.update(selftext="duplicate body", original_content="duplicate body", description="duplicate body")
    raw = _raw_item().model_copy(update={"source_platform": "zsxq", "raw_metadata": metadata})
    card = build_information_card(normalize_raw_item(raw), raw).model_copy(update={"transcript_fallback": True})
    compact = information_card_payload(card)
    legacy = {**compact, "raw_payload": card.model_dump(mode="json")}
    assert set(compact["raw_payload"]) == {"raw_payload", "source_metadata_hash", "transcript_fallback"}
    expected = compact_card_metadata(card.raw_payload)
    assert compact["raw_payload"]["raw_payload"] == expected
    assert CARD_METADATA_FIELDS | CARD_COUNTER_FIELDS | set(CARD_METADATA_CONTAINERS) == set(expected)
    assert "duplicate body" not in str(compact["raw_payload"])
    for container in CARD_METADATA_CONTAINERS:
        assert expected[container] == {"is_ad": True, "ad_id": "ad-1", "likes": 7, **(
            {"content_source": "feishu", "content_source_status": "partial"} if container == "external_doc_read" else {})}
    for platform in ("x", "youtube", "douyin", "xiaohongshu", "scys", "zsxq", "reddit", "hackernews"):
        assert extract_engagement_metrics(platform, compact["raw_payload"]) == extract_engagement_metrics(platform, legacy["raw_payload"])
    assert advertising_projection(compact["raw_payload"]) == advertising_projection(legacy["raw_payload"])
    assert card_is_digest(compact) is card_is_digest(legacy) is True
    repo = CardRepository(tmp_path)
    for payload in (legacy, compact):
        repo.upsert_card(payload)
        stored = repo.get_card(card.card_id)
        assert stored["content_quality"] == "thin"
        assert stored["content_needs_enrichment"] is True
        from backend.domain.feishu_content import feishu_gap
        assert feishu_gap(stored["raw_payload"]) == "external_doc_partial"
