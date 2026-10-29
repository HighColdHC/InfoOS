from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.enrichment.media.download import download_card_media
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository


def test_download_xiaohongshu_card_media_uses_opencli_download(monkeypatch, tmp_path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_xhs_media",
            "source_id": None,
            "source_platform": "xiaohongshu",
            "source_type": "social_post",
            "source_url": "https://www.xiaohongshu.com/search_result/1?xsec_token=abc",
            "source_author": "author",
            "title": "小红书笔记",
            "content": "正文",
            "summary": "摘要",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-02T01:00:00Z",
            "captured_at": "2026-07-02T01:01:00Z",
            "processed_at": "2026-07-02T01:02:00Z",
            "raw_payload": {},
        }
    )
    calls = []
    lease_kwargs = []

    def fake_run(command, **kwargs):
        calls.append(command)
        output_dir = Path(command[command.index("--output") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "image_1.jpg").write_bytes(b"\xff\xd8\xff\xe0image")
        (output_dir / "video_1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42video")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr("backend.application.enrichment.media.xiaohongshu.subprocess.run", fake_run)
    monkeypatch.setattr(
        "backend.application.enrichment.media.xiaohongshu.opencli_tab_lease",
        lambda *_args, **kwargs: lease_kwargs.append(kwargs) or nullcontext(),
    )

    blocks = download_card_media("card_xhs_media", tmp_path)

    assert calls[0][1:3] == ["--profile", "infoos-fetch"]
    assert calls[0][3:5] == ["xiaohongshu", "download"]
    assert calls[0][5] == "https://www.xiaohongshu.com/search_result/1?xsec_token=abc"
    assert calls[0][calls[0].index("--site-session") + 1] == "persistent"
    assert calls[0][calls[0].index("--keep-tab") + 1] == "false"
    assert lease_kwargs == [{"close_tabs": False}]
    assert {block["block_kind"] for block in blocks} == {"image", "video"}
    assets = CardAssetRepository(tmp_path).list_for_card("card_xhs_media")
    assert {asset["asset_kind"] for asset in assets} == {"image", "video"}
    stored_blocks = CardContentBlockRepository(tmp_path).list_for_card("card_xhs_media")
    assert {block["block_kind"] for block in stored_blocks} == {"image", "video"}


def test_download_xiaohongshu_card_media_retries_when_only_non_media_files_exist(monkeypatch, tmp_path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_xhs_retry",
            "source_id": None,
            "source_platform": "xiaohongshu",
            "source_type": "social_post",
            "source_url": "https://www.xiaohongshu.com/search_result/2?xsec_token=retry",
            "source_author": "author",
            "title": "小红书笔记",
            "content": "正文",
            "summary": "摘要",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-02T01:00:00Z",
            "captured_at": "2026-07-02T01:01:00Z",
            "processed_at": "2026-07-02T01:02:00Z",
            "raw_payload": {},
        }
    )
    stale_dir = tmp_path / "data" / "assets" / "card_xhs_retry" / "xiaohongshu"
    stale_dir.mkdir(parents=True)
    (stale_dir / "trace.json").write_text("{}", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        output_dir = Path(command[command.index("--output") + 1])
        (output_dir / "fresh.jpg").write_bytes(b"\xff\xd8\xff\xe0image")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr("backend.application.enrichment.media.xiaohongshu.subprocess.run", fake_run)

    blocks = download_card_media("card_xhs_retry", tmp_path)

    assert calls
    assert [block["block_kind"] for block in blocks] == ["image"]


def test_download_xiaohongshu_card_media_records_gap_on_opencli_failure(monkeypatch, tmp_path):
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_xhs_media_gap",
            "source_id": None,
            "source_platform": "xiaohongshu",
            "source_type": "social_post",
            "source_url": "https://www.xiaohongshu.com/search_result/3?xsec_token=gap",
            "source_author": "author",
            "title": "小红书笔记",
            "content": "正文",
            "summary": "摘要",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-02T01:00:00Z",
            "captured_at": "2026-07-02T01:01:00Z",
            "processed_at": "2026-07-02T01:02:00Z",
            "raw_payload": {},
        }
    )

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="AUTH_REQUIRED")

    monkeypatch.setattr("backend.application.enrichment.media.xiaohongshu.subprocess.run", fake_run)

    blocks = download_card_media("card_xhs_media_gap", tmp_path)

    assert blocks == []
    stored_blocks = CardContentBlockRepository(tmp_path).list_for_card("card_xhs_media_gap")
    gap = next(block for block in stored_blocks if block["block_kind"] == "media_download_gap")
    assert gap["status"] == "blocked"
    assert gap["raw_payload"]["reason"] == "xiaohongshu_media_download_failed"
    assert gap["raw_payload"]["details"]["stderr_summary"] == "AUTH_REQUIRED"
    assert "stderr" not in gap["raw_payload"]["details"]


def test_xiaohongshu_opencli_failure_redacts_diagnostic_streams(monkeypatch, tmp_path):
    CardRepository(tmp_path).upsert_card({
        "card_id": "card_xhs_safe_gap", "source_platform": "xiaohongshu",
        "source_type": "social_post", "source_url": "https://www.xiaohongshu.com/explore/1",
        "title": "post", "content": "body", "captured_at": "2026-09-25T00:00:00Z",
        "processed_at": "2026-09-25T00:00:00Z", "raw_payload": {},
    })

    def failed_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=1, stdout="request https://api.example/items?cursor=secret-value",
            stderr="Cookie: sessionid=private\nAuthorization: Bearer secret-value\ncontact a@b.example +1 415 555 1234",
        )

    monkeypatch.setattr("backend.application.enrichment.media.xiaohongshu.subprocess.run", failed_run)
    assert download_card_media("card_xhs_safe_gap", tmp_path) == []
    gap = next(block for block in CardContentBlockRepository(tmp_path).list_for_card("card_xhs_safe_gap")
               if block["block_kind"] == "media_download_gap")
    details = gap["raw_payload"]["details"]
    assert "stderr" not in details and "stdout" not in details
    summary = details["stderr_summary"]
    assert len(summary) <= 800
    for secret in ("private", "secret-value", "a@b.example", "415 555 1234"):
        assert secret not in summary
