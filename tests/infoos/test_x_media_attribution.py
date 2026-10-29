from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from backend.application.enrichment.media import download
from backend.application.enrichment.media.candidates import collect_media_candidates
from backend.application.enrichment.media.x_attribution import _cross_card_duplicate
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
)


JPEG = b"\xff\xd8\xff\xe0attributed-image"


def _card(base_dir: Path, card_id: str, handle: str, tweet_id: str, raw_payload: dict | None = None) -> dict:
    card = {
        "card_id": card_id,
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": f"https://x.com/{handle}/status/{tweet_id}",
        "source_author": handle,
        "title": "post",
        "content": "body",
        "captured_at": "2026-09-25T00:00:00Z",
        "processed_at": "2026-09-25T00:00:00Z",
        "raw_payload": raw_payload or {},
    }
    CardRepository(base_dir).upsert_card(card)
    return card


def test_target_record_top_level_media_and_matching_poster_only(tmp_path):
    video = "https://video.twimg.com/amplify_video/123/vid/avc1/720x720/clip.mp4?tag=29"
    image = "https://pbs.twimg.com/media/photo.jpg"
    poster = "https://pbs.twimg.com/amplify_video_thumb/123/img/poster.jpg"
    record = {
        "id": "99", "url": "https://x.com/alice/status/99",
        "media_urls": [video, image, "https://pbs.twimg.com/profile_banners/1/banner.jpg"],
        "media_posters": [poster, "https://pbs.twimg.com/amplify_video_thumb/456/img/wrong.jpg",
                          "https://pbs.twimg.com/card_img/card.jpg"],
    }
    card = _card(tmp_path, "card_99", "alice", "99", {"opencli_record": record})
    assert [candidate.url for candidate in collect_media_candidates(card, tmp_path)] == [video, image, poster]
    record["url"] = "https://x.com/alice/status/100"
    assert collect_media_candidates(card, tmp_path) == []


def test_opencli_files_require_per_row_mapping_and_preserve_cdn_url(tmp_path, monkeypatch):
    card = _card(tmp_path, "card_99", "alice", "99")
    cdn = "https://pbs.twimg.com/media/target.jpg"

    def lifecycle(**kwargs):
        attempt = kwargs["staging_root"] / "attempt"
        staged = attempt / "tweets"
        staged.mkdir(parents=True)
        (staged / "tweet_1.jpg").write_bytes(JPEG)
        (staged / "unmapped.jpg").write_bytes(b"\xff\xd8\xff\xe0advert")
        rows = [{"index": 1, "tweet_id": "99", "url": cdn, "type": "image", "status": "success"}]
        return SimpleNamespace(command_result=SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr=""),
                               failure_reason=None, details={"strategy": "opencli"}, attempt_dir=attempt)

    monkeypatch.setattr(download, "run_x_media_download_lifecycle", lifecycle)
    monkeypatch.setattr(download, "run_x_yt_dlp_fallback", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected yt-dlp")))
    blocks = download.download_x_card_media(card["card_id"], card["source_url"], tmp_path)
    assert len(blocks) == 1
    assets = CardAssetRepository(tmp_path).list_for_card(card["card_id"])
    assert len(assets) == 1
    assert assets[0]["remote_url"] == cdn
    assert assets[0]["raw_payload"]["remote_url"] == cdn
    gaps = [block for block in CardContentBlockRepository(tmp_path).list_for_card(card["card_id"])
            if block["block_kind"] == "media_download_gap"]
    assert gaps and gaps[0]["raw_payload"]["reason"] == "x_media_unattributed"


def test_cross_card_duplicate_requires_three_accounts_and_allows_explicit_quote(tmp_path):
    digest = hashlib.sha256(JPEG).hexdigest()
    for index, handle in enumerate(("alice", "bob", "charlie"), start=1):
        card = _card(tmp_path, f"card_{index}", handle, str(index))
        CardAssetRepository(tmp_path).upsert_asset({
            "asset_id": f"asset_{index}", "card_id": card["card_id"], "asset_kind": "image",
            "source_url": card["source_url"], "content_hash": f"sha256:{digest}", "status": "downloaded",
        })
    current = _card(tmp_path, "card_4", "dora", "4")
    assert _cross_card_duplicate(tmp_path, current["card_id"], current["source_url"], digest)["account_count"] == 3
    CardContentBlockRepository(tmp_path).upsert_block({
        "block_id": "block_card_4_quote", "card_id": current["card_id"],
        "position": 2, "layer": "context", "block_kind": "quote", "status": "enriched",
        "body": "quoted", "source_url": "https://x.com/alice/status/1",
        "raw_payload": {"content_role": "quoted", "relation_depth": 1,
                        "relation_source_url": "https://x.com/alice/status/1"},
    })
    assert _cross_card_duplicate(tmp_path, current["card_id"], current["source_url"], digest) is None
