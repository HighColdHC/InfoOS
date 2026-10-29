from __future__ import annotations

from pathlib import Path

from backend.application.enrichment.media.download import download_card_media
from backend.application.enrichment.media.tooling import DownloadedMediaFile, MediaToolResult
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository


def _reddit_card(tmp_path: Path, card_id: str = "card_reddit_gallery") -> None:
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": card_id,
            "source_id": None,
            "source_platform": "reddit",
            "source_type": "social_post",
            "source_url": "https://www.reddit.com/gallery/example",
            "source_author": "reddit_user",
            "title": "Reddit gallery",
            "content": "",
            "summary": "",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-07-06T00:00:00Z",
            "captured_at": "2026-07-06T00:00:01Z",
            "processed_at": "2026-07-06T00:00:02Z",
            "raw_payload": {"post_hint": "image"},
        }
    )


def test_reddit_gallery_falls_back_to_gallery_dl(monkeypatch, tmp_path):
    _reddit_card(tmp_path)
    profile_dir = tmp_path / "chrome-profile"
    monkeypatch.setattr(
        "backend.application.enrichment.media.gallery.chrome_profile_dir",
        lambda: profile_dir,
    )

    def fake_run(url, output_dir, *, cookies_from_browser=None):
        assert cookies_from_browser == f"chrome:{profile_dir}"
        image_path = output_dir / "gallery.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"\xff\xd8\xff\xe0image")
        return MediaToolResult(
            status="downloaded",
            files=[
                DownloadedMediaFile(
                    image_path,
                    "card_reddit_gallery/gallery/gallery.jpg",
                    "image",
                    "image/jpeg",
                    image_path.stat().st_size,
                )
            ],
        )

    monkeypatch.setattr("backend.application.enrichment.media.gallery.run_gallery_dl_download", fake_run)

    blocks = download_card_media("card_reddit_gallery", tmp_path)

    assert [block["block_kind"] for block in blocks] == ["image"]
    asset = CardAssetRepository(tmp_path).list_for_card("card_reddit_gallery")[0]
    assert asset["raw_payload"]["strategy"] == "gallery_dl"


def test_reddit_text_post_does_not_invoke_gallery_downloader(monkeypatch, tmp_path):
    _reddit_card(tmp_path, "card_reddit_text")
    card = CardRepository(tmp_path).get_card("card_reddit_text")
    assert card is not None
    card["source_url"] = "https://www.reddit.com/r/OpenAI/comments/example/text_post/"
    card["raw_payload"] = {}
    CardRepository(tmp_path).upsert_card(card)
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.download_gallery_card_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("text-only Reddit posts must not enter gallery-dl")
        ),
    )

    assert download_card_media("card_reddit_text", tmp_path) == []
    assert not any(
        block["block_kind"] == "media_download_gap"
        for block in CardContentBlockRepository(tmp_path).list_for_card("card_reddit_text")
    )


def test_reddit_gallery_missing_tool_records_gap(monkeypatch, tmp_path):
    _reddit_card(tmp_path, "card_reddit_gap")
    monkeypatch.setattr(
        "backend.application.enrichment.media.gallery.run_gallery_dl_download",
        lambda *args, **kwargs: MediaToolResult(status="blocked", files=[], reason="gallery_dl_missing"),
    )

    blocks = download_card_media("card_reddit_gap", tmp_path)

    assert blocks == []
    gap = next(
        block
        for block in CardContentBlockRepository(tmp_path).list_for_card("card_reddit_gap")
        if block["block_kind"] == "media_download_gap"
    )
    assert gap["raw_payload"]["reason"] == "gallery_dl_missing"
