from backend.domain.information_cards import (
    canonical_information_card_payload,
    information_card_content_hash,
)


def _card() -> dict:
    return {
        "card_id": "card_1",
        "card_type": "information",
        "source_id": "source_1",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "HTTPS://Example.COM:443/post/1?b=2&a=1#ignored",
        "source_author": "alice",
        "title": "A title",
        "content": "Readable body",
        "summary": "A summary",
        "published_at": "2026-07-16T09:00:00Z",
        "captured_at": "2026-07-16T09:01:00Z",
        "processed_at": "2026-07-16T09:02:00Z",
        "updated_at": "2026-07-16T09:03:00Z",
        "source_run_id": "run_1",
        "raw_connector_id": "connector_1",
        "raw_item_id": "raw_1",
    }


def _blocks() -> list[dict]:
    return [
        {
            "block_id": "block_2",
            "position": 1,
            "layer": "source",
            "block_kind": "quote",
            "status": "ready",
            "body": "Second",
            "segments": [{"text": "segment"}],
            "source_url": "https://example.com/post/1",
            "provenance": "source",
            "created_at": "2026-07-16T09:02:00Z",
            "updated_at": "2026-07-16T09:03:00Z",
        },
        {
            "block_id": "block_1",
            "position": 0,
            "layer": "source",
            "block_kind": "text",
            "status": "ready",
            "body": "First",
            "segments": [],
            "source_url": "https://example.com/post/1",
            "provenance": "source",
        },
    ]


def _assets() -> list[dict]:
    return [
        {
            "asset_id": "video_1",
            "asset_kind": "video",
            "source_url": "https://example.com/video/1",
            "remote_url": "https://cdn.example.com/video/1.mp4",
            "local_path": "/tmp/run-1/video.mp4",
            "mime_type": "video/mp4",
            "size_bytes": 1024,
            "content_hash": "sha256:video",
            "preservation_class": "cached",
            "status": "downloaded",
            "created_at": "2026-07-16T09:02:00Z",
        },
        {
            "asset_id": "image_1",
            "asset_kind": "image",
            "source_url": "https://example.com/image/1",
            "remote_url": "https://cdn.example.com/image/1.jpg",
            "local_path": "/tmp/run-1/image.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 512,
            "content_hash": "sha256:image",
            "preservation_class": "cached",
            "status": "downloaded",
        },
    ]


def test_canonical_information_card_payload_keeps_only_deliverable_stable_facts():
    payload = canonical_information_card_payload(
        _card(),
        _blocks(),
        _assets(),
        completeness_status="partial",
        missing_reasons=["video_transcript_missing", "image_description_missing"],
    )

    assert payload["source_identity"] == {"source_url": "https://example.com/post/1?a=1&b=2"}
    assert [block["body"] for block in payload["content_blocks"]] == ["First", "Second"]
    assert [asset["asset_kind"] for asset in payload["assets"]] == ["image", "video"]
    assert payload["missing_reasons"] == ["image_description_missing", "video_transcript_missing"]
    serialized = repr(payload)
    for runtime_fact in ("run_1", "/tmp/run-1", "captured_at", "processed_at", "updated_at", "'status':"):
        assert runtime_fact not in serialized


def test_information_card_hash_ignores_order_and_runtime_noise_but_changes_for_readable_content():
    original = information_card_content_hash(
        _card(),
        _blocks(),
        _assets(),
        completeness_status="complete",
        missing_reasons=[],
    )
    noisy_card = _card() | {
        "source_run_id": "run_99",
        "captured_at": "2030-01-01T00:00:00Z",
        "processed_at": "2030-01-01T00:00:01Z",
        "updated_at": "2030-01-01T00:00:02Z",
        "attempt": 99,
        "log": "different log",
    }
    noisy_blocks = list(reversed(_blocks()))
    noisy_blocks[0] = noisy_blocks[0] | {"updated_at": "2030-01-01T00:00:03Z", "status": "retrying"}
    noisy_assets = list(reversed(_assets()))
    noisy_assets[0] = noisy_assets[0] | {
        "local_path": "/a/different/absolute/path.jpg",
        "updated_at": "2030-01-01T00:00:04Z",
        "status": "processing",
    }

    same = information_card_content_hash(
        noisy_card,
        noisy_blocks,
        noisy_assets,
        completeness_status="complete",
        missing_reasons=[],
    )
    changed_blocks = [block | ({"body": "Changed"} if block["block_id"] == "block_1" else {}) for block in _blocks()]
    changed = information_card_content_hash(
        _card(),
        changed_blocks,
        _assets(),
        completeness_status="complete",
        missing_reasons=[],
    )

    assert original.startswith("sha256:")
    assert same == original
    assert changed != original


def test_information_card_hash_falls_back_to_raw_item_identity_without_source_url():
    card = _card() | {"source_url": ""}

    payload = canonical_information_card_payload(
        card,
        [],
        [],
        completeness_status="complete",
        missing_reasons=[],
    )

    assert payload["source_identity"] == {
        "connector_id": "connector_1",
        "raw_item_id": "raw_1",
    }
