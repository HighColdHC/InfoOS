import urllib3

from backend.application.enrichment.media import artifacts, douyin
from backend.application.enrichment.media.candidates import collect_media_candidates
from backend.infrastructure.storage.repositories import CardRepository


def test_douyin_candidates_keep_order_and_download_after_403(tmp_path, monkeypatch):
    card = {
        "card_id": "feed-video", "source_platform": "douyin", "source_type": "video",
        "source_url": "https://www.douyin.com/video/123", "title": "video", "content": "body",
        "captured_at": "2026-10-09T02:00:00Z", "processed_at": "2026-10-09T02:00:00Z",
        "raw_payload": {"raw_payload": {"play_url": "https://cdn.test/api", "play_urls": [
            "https://cdn.test/api", "//cdn.test/second", "https://cdn.test/third",
        ]}},
    }
    CardRepository(tmp_path).upsert_card(card)
    candidates = collect_media_candidates(card, tmp_path)
    assert [(item.url, item.kind) for item in candidates] == [
        ("https://cdn.test/api", "video"), ("https://cdn.test/second", "video"), ("https://cdn.test/third", "video"),
    ]
    monkeypatch.setattr(artifacts.socket, "getaddrinfo",
                        lambda host, port, **kwargs: [(2, 1, 6, "", ("93.184.216.34", port))])
    calls = []

    class Response:
        headers = {"content-type": "video/mp4"}

        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code == 403:
                raise urllib3.exceptions.HTTPError("403 Forbidden")

        def iter_content(self, chunk_size):
            yield b"\x00\x00\x00\x18ftypmp42video"

        def close(self):
            pass

    def get(url, **kwargs):
        calls.append((url, kwargs["headers"]))
        return Response(403 if len(calls) == 1 else 206)

    monkeypatch.setattr(artifacts.requests, "get", get)
    monkeypatch.setattr(douyin, "download_douyin_with_ytdlp", lambda *args: (_ for _ in ()).throw(AssertionError("unexpected fallback")))
    blocks = douyin.download_douyin_card_media(card, tmp_path)
    assert [url for url, _ in calls] == ["https://cdn.test/api", "https://cdn.test/second"]
    assert len(blocks) == 1
    assert blocks[0]["raw_payload"]["strategy"] == "direct_candidate"
    assert all(headers == {"User-Agent": "Mozilla/5.0", "Referer": card["source_url"]} for _, headers in calls)
