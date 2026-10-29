from __future__ import annotations

from backend.application.tasks import input_versions
from backend.application.tasks.input_versions import (
    card_build_input_version,
    card_finalize_input_version,
    enrichment_input_version,
    media_input_version,
)


def test_enrichment_and_media_versions_ignore_finalize_runtime_metadata() -> None:
    processing = {
        "card_id": "card-1",
        "source_platform": "rss",
        "content": "Stable",
        "status": "processing",
        "version": 0,
        "raw_payload": {"processed_at": "first", "source_run_id": "run-1"},
    }
    active = {
        **processing,
        "status": "active",
        "version": 1,
        "content_hash": "sha256:abc",
        "updated_at": "second",
        "raw_payload": {"processed_at": "second", "source_run_id": "run-2"},
    }

    enrich_version = enrichment_input_version(processing)
    assert enrichment_input_version(active) == enrich_version
    assert media_input_version(card=processing, enrichment_version=enrich_version) == media_input_version(
        card=active, enrichment_version=enrich_version
    )


def test_card_finalize_input_version_ignores_runtime_noise() -> None:
    card = {"card_id": "card-1", "content": "Stable", "updated_at": "first", "source_run_id": "run-1"}
    blocks = [{"block_id": "body", "position": 0, "body": "Stable", "updated_at": "first"}]
    assets = [{"asset_id": "asset-1", "asset_kind": "image", "content_hash": "sha256:a", "local_path": "/tmp/one"}]

    assert card_finalize_input_version(card, blocks, assets) == card_finalize_input_version(
        {**card, "updated_at": "second", "source_run_id": "run-2"},
        [{**blocks[0], "updated_at": "second"}],
        [{**assets[0], "local_path": "/another/machine/two"}],
    )


def test_card_finalize_input_version_changes_with_deliverable_content() -> None:
    card = {"card_id": "card-1", "content": "Stable"}

    assert card_finalize_input_version(card, [], []) != card_finalize_input_version(
        {**card, "content": "Changed"}, [], []
    )


def test_card_finalize_input_version_changes_with_policy(monkeypatch) -> None:
    card = {"card_id": "card-1", "content": "Stable"}
    current = card_finalize_input_version(card, [], [])

    monkeypatch.setattr(input_versions, "CARD_FINALIZE_POLICY_VERSION", "card-finalize-v-next")

    assert card_finalize_input_version(card, [], []) != current


def test_same_card_state_produces_same_input_version() -> None:
    card = {"raw_item_id": "raw-1", "content": "A stable card", "title": "Stable"}

    assert card_build_input_version(card) == card_build_input_version(dict(card))
