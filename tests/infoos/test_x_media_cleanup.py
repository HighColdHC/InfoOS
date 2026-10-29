from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskType
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
)
from backend.infrastructure.storage.x_media_cleanup import XMediaCleanupStore
from backend.workers.x_media_cleanup import apply_cleanup, main


BAD_HASH = "sha256:" + "5" * 64


def _card(card_id: str, *, platform: str = "x", version: int = 4) -> dict:
    return {
        "card_id": card_id,
        "version": version,
        "source_platform": platform,
        "source_type": "social_post",
        "source_url": f"https://x.com/example/status/{card_id}",
        "source_author": "example",
        "title": "Post",
        "content": "Body",
        "summary": "Summary",
        "published_at": "2026-09-25T00:00:00Z",
        "captured_at": "2026-09-25T00:00:00Z",
        "processed_at": "2026-09-25T00:00:00Z",
        "workflow_status": "new",
        "raw_payload": {},
    }


def _asset(base_dir: Path, *, asset_id: str, card_id: str, content_hash: str, local_path: str) -> None:
    CardAssetRepository(base_dir).upsert_asset(
        {
            "asset_id": asset_id,
            "card_id": card_id,
            "asset_kind": "image",
            "source_url": f"https://x.com/example/status/{card_id}",
            "remote_url": "https://pbs.twimg.com/media/example.jpg",
            "local_path": local_path,
            "mime_type": "image/jpeg",
            "size_bytes": 4,
            "content_hash": content_hash,
            "status": "downloaded",
            "raw_payload": {"strategy": "opencli_fallback"},
        }
    )


def _block(
    base_dir: Path,
    *,
    block_id: str,
    card_id: str,
    layer: str,
    block_kind: str,
    body: str,
    source_url: str | None = None,
    raw_payload: dict | None = None,
) -> None:
    CardContentBlockRepository(base_dir).upsert_block(
        {
            "block_id": block_id,
            "card_id": card_id,
            "position": 1,
            "layer": layer,
            "block_kind": block_kind,
            "status": "enriched",
            "body": body,
            "source_url": source_url,
            "raw_payload": raw_payload or {},
        }
    )


def _seed_cleanup_case(tmp_path: Path) -> Path:
    cards = CardRepository(tmp_path)
    cards.upsert_card(_card("card_x"))
    cards.upsert_card(_card("card_rss", platform="rss"))
    bad_relative = "card_x/bad.jpg"
    bad_file = tmp_path / "data/assets" / bad_relative
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_bytes(b"bad!")
    _asset(tmp_path, asset_id="asset_bad", card_id="card_x", content_hash=BAD_HASH, local_path=bad_relative)
    _asset(
        tmp_path,
        asset_id="asset_good",
        card_id="card_x",
        content_hash="sha256:" + "6" * 64,
        local_path="card_x/good.jpg",
    )
    _block(
        tmp_path,
        block_id="image_bad",
        card_id="card_x",
        layer="media",
        block_kind="image",
        body=bad_relative,
    )
    _block(
        tmp_path,
        block_id="ocr_bad",
        card_id="card_x",
        layer="derived",
        block_kind="image_ocr",
        body="Starlink promotion",
        source_url="asset:asset_bad",
    )
    _block(
        tmp_path,
        block_id="description_bad",
        card_id="card_x",
        layer="derived",
        block_kind="image_description",
        body="SpaceX logo",
        source_url="asset:asset_bad",
    )
    _block(
        tmp_path,
        block_id="ai_summary",
        card_id="card_x",
        layer="intelligence",
        block_kind="summary",
        body="This post may contain a Starlink advertisement.",
        raw_payload={"purpose": "ai_interpretation"},
    )
    return bad_file


def test_preview_lists_bounded_x_rows_and_derived_keyword_mentions(tmp_path: Path) -> None:
    _seed_cleanup_case(tmp_path)

    plan = XMediaCleanupStore(tmp_path).plan(BAD_HASH)

    assert plan["affected_card_count"] == 1
    assert plan["affected_asset_count"] == 1
    affected = plan["affected_cards"][0]
    assert affected["card_id"] == "card_x"
    assert [row["asset_id"] for row in affected["assets"]] == ["asset_bad"]
    assert [row["asset_id"] for row in affected["other_assets"]] == ["asset_good"]
    assert [row["block_id"] for row in affected["image_blocks"]] == ["image_bad"]
    assert {row["block_id"] for row in affected["linked_image_blocks"]} == {
        "ocr_bad",
        "description_bad",
    }
    scan = affected["derived_text_keyword_scan"]
    assert scan["has_ad_keywords"] is True
    assert {row["block_id"]: row["keywords"] for row in scan["matches"]} == {
        "ocr_bad": ["starlink"],
        "description_bad": ["spacex"],
        "ai_summary": ["starlink"],
    }


def test_apply_quarantines_files_deletes_exact_rows_and_enqueues_finalize(tmp_path: Path) -> None:
    bad_file = _seed_cleanup_case(tmp_path)
    plan = XMediaCleanupStore(tmp_path).plan(BAD_HASH)

    result = apply_cleanup(
        tmp_path,
        BAD_HASH,
        plan=plan,
        now=datetime(2026, 9, 25, 12, 34, 56, tzinfo=timezone.utc),
    )

    assert not bad_file.exists()
    quarantine = tmp_path / "data/quarantine/x_media_cleanup/20260925T123456Z"
    assert (quarantine / "assets/card_x/bad.jpg").read_bytes() == b"bad!"
    manifest = json.loads((quarantine / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "applied"
    assert manifest["deleted_rows"]["card_assets"][0]["asset_id"] == "asset_bad"
    assert {row["block_id"] for row in manifest["deleted_rows"]["card_content_blocks"]} == {
        "image_bad",
        "ocr_bad",
        "description_bad",
    }
    assert result["deleted_assets"] == 1
    assert result["deleted_blocks"] == 3

    assert CardAssetRepository(tmp_path).get_asset("asset_bad") is None
    assert CardAssetRepository(tmp_path).get_asset("asset_good") is not None
    remaining_blocks = CardContentBlockRepository(tmp_path).list_for_card("card_x")
    assert [block["block_id"] for block in remaining_blocks] == ["ai_summary"]
    assert CardRepository(tmp_path).get_card("card_x")["version"] == 5

    tasks = TaskRepository.for_base_dir(tmp_path).list_tasks(limit=20)
    assert len(tasks) == 1
    assert tasks[0].task_type == TaskType.CARD_FINALIZE
    assert tasks[0].target_id == "card_x"
    assert tasks[0].artifacts_summary["maintenance_reason"] == "x_media_cleanup"
    assert all(task.task_type != TaskType.CARD_LLM_ENRICH for task in tasks)


def test_preview_refuses_hash_owned_by_any_non_x_card(tmp_path: Path) -> None:
    _seed_cleanup_case(tmp_path)
    _asset(
        tmp_path,
        asset_id="asset_non_x",
        card_id="card_rss",
        content_hash=BAD_HASH,
        local_path="card_rss/same.jpg",
    )

    with pytest.raises(ValueError, match="non-X cards.*card_rss/asset_non_x"):
        XMediaCleanupStore(tmp_path).plan(BAD_HASH)


@pytest.mark.parametrize("file_state", ["missing", "outside"])
def test_apply_refuses_to_delete_when_asset_file_cannot_be_quarantined(
    tmp_path: Path, file_state: str,
) -> None:
    original = _seed_cleanup_case(tmp_path)
    asset_repo = CardAssetRepository(tmp_path)
    asset = asset_repo.get_asset("asset_bad")
    assert asset is not None
    if file_state == "missing":
        original.unlink()
    else:
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(b"bad!")
        asset_repo.upsert_asset({**asset, "local_path": str(outside)})

    with pytest.raises((ValueError, FileNotFoundError)):
        apply_cleanup(
            tmp_path, BAD_HASH,
            now=datetime(2026, 9, 25, 13, 0, 0, tzinfo=timezone.utc),
        )

    assert asset_repo.get_asset("asset_bad") is not None
    assert CardRepository(tmp_path).get_card("card_x")["version"] == 4
    assert TaskRepository.for_base_dir(tmp_path).list_tasks(limit=20) == []
    if file_state == "outside":
        assert outside.read_bytes() == b"bad!"


def test_apply_restores_moved_file_when_database_transaction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_file = _seed_cleanup_case(tmp_path)

    def fail_delete(*_args, **_kwargs):
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(XMediaCleanupStore, "delete_and_increment", fail_delete)

    with pytest.raises(RuntimeError, match="simulated transaction failure"):
        apply_cleanup(
            tmp_path,
            BAD_HASH,
            now=datetime(2026, 9, 25, 12, 35, 0, tzinfo=timezone.utc),
        )

    assert bad_file.read_bytes() == b"bad!"
    manifest_path = (
        tmp_path
        / "data/quarantine/x_media_cleanup/20260925T123500Z/manifest.json"
    )
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "rolled_back"
    assert CardAssetRepository(tmp_path).get_asset("asset_bad") is not None
    assert CardRepository(tmp_path).get_card("card_x")["version"] == 4
    assert TaskRepository.for_base_dir(tmp_path).list_tasks(limit=20) == []


def test_cli_requires_an_exact_content_hash(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--base-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        main(["--base-dir", str(tmp_path), "--content-hash", "sha256:1234"])
