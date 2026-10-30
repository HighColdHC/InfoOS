from __future__ import annotations

import sqlite3

from backend.infrastructure.storage.repositories import CardRepository, SourceRepository


def _card_payload(*, card_id: str, source_id: str, source_url: str = "https://example.com/a") -> dict:
    return {
        "card_id": card_id,
        "source_id": source_id,
        "source_platform": "rss",
        "source_type": "article",
        "source_url": source_url,
        "source_author": "Example",
        "title": f"Title from {source_id}",
        "content": f"Content from {source_id}",
        "summary": f"Summary from {source_id}",
        "published_at": "2026-07-01T00:00:00Z",
        "captured_at": "2026-07-01T00:01:00Z",
        "processed_at": "2026-07-01T00:02:00Z",
        "workflow_status": "routed",
        "raw_payload": {"source_id": source_id},
    }


def test_get_card_platform_returns_platform_or_none(tmp_path):
    repo = CardRepository(tmp_path)
    repo.upsert_card(_card_payload(card_id="platform_only", source_id=None))
    assert repo.get_card_platform("platform_only") == "rss"
    assert repo.get_card_platform("missing") is None


def test_upsert_card_refreshes_source_identity_on_existing_card(tmp_path):
    source_repo = SourceRepository(tmp_path)
    for source_id in ("source_old", "source_new"):
        source_repo.upsert_source(
            {
                "source_id": source_id,
                "platform": "rss",
                "source_type": "feed",
                "identifier": f"https://example.com/{source_id}.xml",
                "display_name": source_id,
                "cadence_hours": 24,
                "enabled": True,
                "connector_status": "supported",
                "connector_id": source_id,
            }
        )
    repo = CardRepository(tmp_path)
    repo.upsert_card(_card_payload(card_id="card_same", source_id="source_old"))

    repo.upsert_card(
        _card_payload(
            card_id="card_same",
            source_id="source_new",
            source_url="https://example.com/a?from=new",
        )
    )

    card = repo.get_card("card_same")
    assert card is not None
    assert card["source_id"] == "source_new"
    assert card["source_url"] == "https://example.com/a?from=new"
    assert card["title"] == "Title from source_new"


def test_finalize_card_creates_first_active_version_and_decodes_delivery_fields(tmp_path):
    repo = CardRepository(tmp_path)
    payload = _card_payload(card_id="card_finalize", source_id=None)
    payload.update(
        {
            "raw_connector_id": "rss_connector",
            "raw_item_id": "item_1",
            "source_run_id": "run_1",
            "processor_version": "collector-v1",
        }
    )
    repo.upsert_card(payload)

    result = repo.finalize_card(
        "card_finalize",
        content_hash="sha256:first",
        completeness_status="partial",
        missing_reasons=["video_transcript_missing"],
        processor_version="collector-v1",
        source_run_id="run_1",
        updated_at="2026-07-16T10:00:00Z",
    )

    assert result == {
        "card_id": "card_finalize",
        "version": 1,
        "content_hash": "sha256:first",
        "updated_at": "2026-07-16T10:00:00Z",
        "status": "active",
        "completeness_status": "partial",
        "missing_reasons": ["video_transcript_missing"],
        "processor_version": "collector-v1",
        "source_run_id": "run_1",
        "changed": True,
    }
    stored = repo.get_card("card_finalize")
    assert stored is not None
    assert stored["card_type"] == "information"
    assert stored["raw_connector_id"] == "rss_connector"
    assert stored["raw_item_id"] == "item_1"
    assert stored["missing_reasons"] == ["video_transcript_missing"]


def test_finalize_card_same_hash_keeps_version_hash_and_updated_at(tmp_path):
    repo = CardRepository(tmp_path)
    repo.upsert_card(_card_payload(card_id="card_same_hash", source_id=None))
    first = repo.finalize_card(
        "card_same_hash",
        content_hash="sha256:stable",
        completeness_status="complete",
        missing_reasons=[],
        source_run_id="run_1",
        updated_at="2026-07-16T10:00:00Z",
    )

    repeated = repo.finalize_card(
        "card_same_hash",
        content_hash="sha256:stable",
        completeness_status="complete",
        missing_reasons=[],
        source_run_id="run_2",
        updated_at="2026-07-16T11:00:00Z",
    )

    assert first["version"] == 1
    assert repeated["changed"] is False
    assert repeated["version"] == 1
    assert repeated["content_hash"] == "sha256:stable"
    assert repeated["updated_at"] == "2026-07-16T10:00:00Z"
    assert repeated["source_run_id"] == "run_2"


def test_finalize_card_changed_hash_increments_exactly_one_version(tmp_path):
    repo = CardRepository(tmp_path)
    repo.upsert_card(_card_payload(card_id="card_changed", source_id=None))
    repo.finalize_card(
        "card_changed",
        content_hash="sha256:v1",
        completeness_status="complete",
        missing_reasons=[],
        updated_at="2026-07-16T10:00:00Z",
    )

    changed = repo.finalize_card(
        "card_changed",
        content_hash="sha256:v2",
        completeness_status="partial",
        missing_reasons=["image_download_failed"],
        updated_at="2026-07-16T11:00:00Z",
    )

    assert changed["changed"] is True
    assert changed["version"] == 2
    assert changed["content_hash"] == "sha256:v2"
    assert changed["completeness_status"] == "partial"
    assert changed["missing_reasons"] == ["image_download_failed"]


def test_finalize_card_uses_caller_owned_transaction_without_committing(tmp_path):
    repo = CardRepository(tmp_path)
    repo.upsert_card(_card_payload(card_id="card_transaction", source_id=None))
    conn = sqlite3.connect(repo.db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = repo.finalize_card(
            "card_transaction",
            content_hash="sha256:uncommitted",
            completeness_status="complete",
            missing_reasons=[],
            updated_at="2026-07-16T12:00:00Z",
            conn=conn,
        )
        assert result["version"] == 1
        conn.rollback()
    finally:
        conn.close()

    stored = repo.get_card("card_transaction")
    assert stored is not None
    assert stored["version"] == 0
    assert stored["content_hash"] is None
    assert stored["status"] == "processing"
