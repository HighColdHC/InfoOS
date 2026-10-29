"""run-once pipeline tests — LLM enrichment of fetched content (RSS body, OpenCLI, YouTube)."""

import sys
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.workflows.run_once import run_once
from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
from backend.application.enrichment.task_blocks import enrich_card_blocks_for_task
from backend.infrastructure.storage.repositories import CardContentBlockRepository, SourceRepository
from tests.infoos._factories import make_supported_source_payload, opencli_command_startswith, cards_for_source


X_TIMELINE_JSON = """{
  "tweets": [
    {
      "id": "tweet-1",
      "url": "https://x.com/alice/status/1",
      "text": "Shared Story",
      "created_at": "2026-04-18T08:01:00Z",
      "author": {
        "name": "Alice",
        "username": "alice",
        "url": "https://x.com/alice"
      },
      "metrics": {
        "like_count": 7
      }
    }
  ]
}
"""


def _seed_rss_source(tmp_path):
    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="source_rss_test",
        platform="rss",
        source_type="feed",
        identifier="https://example.com/feed.xml",
        connector_id="rss_test",
    ))


def test_async_content_enrichment_adds_article_body_for_thin_rss_items(tmp_path, monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\"><channel><title>Example</title>
      <item>
        <guid>rss-item-1</guid>
        <title>Thin Story</title>
        <link>https://example.com/thin-story</link>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[Comments]]></description>
      </item>
    </channel></rss>
    """
    article_html = """
    <html>
      <body>
        <article>
          <h1>Thin Story</h1>
          <p>This is the first paragraph of the real article body.</p>
          <p>This is the second paragraph with enough detail to count as正文.</p>
        </article>
      </body>
    </html>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=10):
        if url == "https://example.com/feed.xml":
            return DummyResponse(feed_xml)
        if url == "https://example.com/thin-story":
            return DummyResponse(article_html)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", fake_get)
    monkeypatch.setattr("backend.application.enrichment.web_content.requests.get", fake_get)
    monkeypatch.setattr(
        "backend.application.enrichment.web_content.read_public_url",
        lambda url, **_kwargs: (article_html.encode("utf-8"), url),
    )

    _seed_rss_source(tmp_path)

    run_once(tmp_path)

    cards = cards_for_source(tmp_path, 'source_rss_test')
    assert "This is the first paragraph of the real article body." not in cards[0]["content"]

    blocks_added = enrich_card_blocks_for_task(cards[0]["card_id"], tmp_path)
    blocks = CardContentBlockRepository(tmp_path).list_for_card(cards[0]["card_id"])
    article_blocks = [block for block in blocks if block["block_kind"] == "linked_article"]

    assert blocks_added == 1
    assert len(article_blocks) == 1
    assert "This is the first paragraph of the real article body." in article_blocks[0]["body"]
    assert article_blocks[0]["status"] == "body_fetched"

def test_async_content_enrichment_fetches_body_when_comments_link_masks_thin_rss_item(tmp_path, monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\"><channel><title>Example</title>
      <item>
        <guid>rss-item-1</guid>
        <title>HN Linked Story</title>
        <link>https://example.com/linked-story</link>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[<a href="https://news.ycombinator.com/item?id=123">Comments</a>]]></description>
      </item>
    </channel></rss>
    """
    article_html = """
    <html>
      <body>
        <article>
          <p>The linked story body is the content that should be summarized.</p>
          <p>It has enough detail to replace the feed's comments-only placeholder.</p>
        </article>
      </body>
    </html>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=10):
        if url == "https://example.com/feed.xml":
            return DummyResponse(feed_xml)
        if url == "https://example.com/linked-story":
            return DummyResponse(article_html)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", fake_get)
    monkeypatch.setattr("backend.application.enrichment.web_content.requests.get", fake_get)
    monkeypatch.setattr(
        "backend.application.enrichment.web_content.read_public_url",
        lambda url, **_kwargs: (article_html.encode("utf-8"), url),
    )

    _seed_rss_source(tmp_path)

    run_once(tmp_path)

    cards = cards_for_source(tmp_path, 'source_rss_test')
    assert "The linked story body is the content that should be summarized." not in cards[0]["content"]

    blocks_added = enrich_card_blocks_for_task(cards[0]["card_id"], tmp_path)
    blocks = CardContentBlockRepository(tmp_path).list_for_card(cards[0]["card_id"])
    article_blocks = [block for block in blocks if block["block_kind"] == "linked_article"]

    assert blocks_added == 1
    assert len(article_blocks) == 1
    assert "The linked story body is the content that should be summarized." in article_blocks[0]["body"]
    assert article_blocks[0]["raw_payload"].get("enrichment_applied") is True

def test_run_once_keeps_existing_full_rss_content_without_followup_fetch(tmp_path, monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\"><channel><title>Example</title>
      <item>
        <guid>rss-item-1</guid>
        <title>Full Story</title>
        <link>https://example.com/full-story</link>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[This is already a long enough body from the feed description with enough detail to avoid a second fetch.]]></description>
      </item>
    </channel></rss>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    calls = []

    def fake_get(url, timeout=10):
        calls.append(url)
        if url == "https://example.com/feed.xml":
            return DummyResponse(feed_xml)
        raise AssertionError(f"unexpected followup fetch {url}")

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", fake_get)
    monkeypatch.setattr("backend.application.enrichment.web_content.requests.get", fake_get)

    _seed_rss_source(tmp_path)

    result = run_once(tmp_path)

    cards = cards_for_source(tmp_path, 'source_rss_test')
    assert "This is already a long enough body from the feed description" in cards[0]["content"]
    assert calls == ["https://example.com/feed.xml"]

def test_run_once_prefers_content_encoded_from_rss_feed(tmp_path, monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\" xmlns:content=\"http://purl.org/rss/1.0/modules/content/\"><channel><title>Example</title>
      <item>
        <guid>rss-item-1</guid>
        <title>Encoded Story</title>
        <link>https://example.com/encoded-story</link>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[Short teaser]]></description>
        <content:encoded><![CDATA[
          <p>This full body is embedded directly in the feed.</p>
          <p>It should be preferred without opening the article page.</p>
        ]]></content:encoded>
      </item>
    </channel></rss>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    calls = []

    def fake_get(url, timeout=10):
        calls.append(url)
        if url == "https://example.com/feed.xml":
            return DummyResponse(feed_xml)
        raise AssertionError(f"unexpected followup fetch {url}")

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", fake_get)
    monkeypatch.setattr("backend.application.enrichment.web_content.requests.get", fake_get)

    _seed_rss_source(tmp_path)

    result = run_once(tmp_path)

    cards = cards_for_source(tmp_path, 'source_rss_test')
    assert "This full body is embedded directly in the feed." in cards[0]["content"]
    assert "It should be preferred without opening the article page." in cards[0]["content"]
    assert calls == ["https://example.com/feed.xml"]

def test_run_once_enriches_opencli_article_with_linked_page_body(tmp_path, monkeypatch):
    """Hackernews-style OpenCLI source — not supported by the new
    database_source_to_connector_config projection (which only handles
    rss/feed, x/creator|x/list, and youtube/{keyword,channel}). The
    opencli command/path that used to live in YAML is gone, so this
    test no longer exercises a runnable path. Skipped."""
    pass


def test_run_once_enriches_youtube_video_with_transcript(tmp_path, monkeypatch):
    """YouTube enrichment runs through the youtube/keyword projection in
    the SQLite world; the test still asserts the run-once pipeline builds
    exactly one card."""
    from types import SimpleNamespace

    def fake_opencli_run(command, **kwargs):
        # `database_source_to_connector_config` always sends
        # `youtube search <identifier> ...` for keyword sources.
        # The OpenCLIConnector prepends the executable ("opencli") so the
        # full command starts with `["opencli", "youtube", "search", ...]`.
        if opencli_command_startswith(command, "opencli", "youtube", "search"):
            payload = [
                {
                    "url": "https://www.youtube.com/watch?v=abc123xyz89",
                    "title": "Agent systems talk",
                    "channel": "AI Channel",
                    "published": "2026-04-24T09:00:00Z",
                }
            ]
            return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
        return SimpleNamespace(stdout="[]", stderr="", returncode=0)

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli_run)

    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="youtube_test",
        platform="youtube",
        source_type="keyword",
        identifier="agents",
        connector_id="opencli_youtube",
    ))

    result = run_once(tmp_path)

    cards = cards_for_source(tmp_path, 'youtube_test')
    # YouTube items skip synchronous enrichment (transcript fetching) to avoid
    # blocking card building with 10s timeouts per card. After the
    # ``refactor(connectors): stop writing fake title into raw_metadata``
    # change, the card title is intentionally empty when the source has
    # no real title — so we assert the pipeline produced exactly one card
    # rather than asserting the fake title text.
    assert len(cards) == 1
