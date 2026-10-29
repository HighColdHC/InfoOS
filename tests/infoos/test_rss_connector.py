import sys
from pathlib import Path

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.enrichment.media import artifacts
from backend.application.enrichment.web_content import enrich_raw_item_content
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.connectors.feed.rss import RSSConnector
from backend.infrastructure.storage.raw_store import RawItemStore


FEED_XML = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\">
  <channel>
    <title>Example Feed</title>
    <link>https://example.com</link>
    <description>Updates</description>
    <item>
      <guid>item-1</guid>
      <title>First Post</title>
      <link>https://example.com/posts/1</link>
      <author>alice@example.com (Alice)</author>
      <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
      <description><![CDATA[<p>Hello <b>world</b>.</p>]]></description>
    </item>
    <item>
      <guid>item-2</guid>
      <title>Second Post</title>
      <link>https://example.com/posts/2</link>
      <pubDate>Fri, 18 Apr 2026 09:00:00 GMT</pubDate>
      <description><![CDATA[<p>Another update.</p>]]></description>
    </item>
  </channel>
</rss>
"""

UPDATED_FEED_XML = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\">
  <channel>
    <title>Example Feed</title>
    <link>https://example.com</link>
    <description>Updates</description>
    <item>
      <guid>item-3</guid>
      <title>Newest Post</title>
      <link>https://example.com/posts/3</link>
      <pubDate>Fri, 18 Apr 2026 10:00:00 GMT</pubDate>
      <description><![CDATA[<p>Fresh update.</p>]]></description>
    </item>
    <item>
      <guid>item-2</guid>
      <title>Second Post</title>
      <link>https://example.com/posts/2</link>
      <pubDate>Fri, 18 Apr 2026 09:00:00 GMT</pubDate>
      <description><![CDATA[<p>Another update.</p>]]></description>
    </item>
    <item>
      <guid>item-1</guid>
      <title>First Post</title>
      <link>https://example.com/posts/1</link>
      <author>alice@example.com (Alice)</author>
      <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
      <description><![CDATA[<p>Hello <b>world</b>.</p>]]></description>
    </item>
  </channel>
</rss>
"""

ATOM_FEED_XML = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<feed xmlns=\"http://www.w3.org/2005/Atom\">
  <title>Example Atom Feed</title>
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.2.0</id>
    <title>v1.2.0</title>
    <link href=\"https://github.com/example/project/releases/tag/v1.2.0\" rel=\"alternate\"/>
    <updated>2026-06-26T01:02:03Z</updated>
    <author><name>github-actions</name></author>
    <content type=\"html\">&lt;p&gt;Release notes for v1.2.0&lt;/p&gt;</content>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.1.0</id>
    <title>v1.1.0</title>
    <link href=\"https://github.com/example/project/releases/tag/v1.1.0\" rel=\"alternate\"/>
    <updated>2026-06-25T01:02:03Z</updated>
  </entry>
</feed>
"""


class DummyResponse:
    def __init__(self, text: str, status_code: int = 200, *, headers=None):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http error: {self.status_code}")

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    def close(self):
        self.closed = True


def _public_address_record(hostname: str, port: int, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


@pytest.fixture
def connector(tmp_path):
    return RSSConnector(
        connector_id="rss_test",
        feed_url="https://example.com/feed.xml",
        data_dir=tmp_path / "data",
    )


def test_rss_connector_fetches_raw_items_and_extracts_fields(monkeypatch, connector):
    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(FEED_XML))

    result = connector.fetch_new_items(since_cursor=None)

    assert len(result.items) == 2
    assert result.items[0].raw_item_id == "item-1"
    assert result.items[0].raw_author.name == "Alice"
    assert result.items[1].raw_timestamps.published_at.startswith("2026-04-18T09:00:00")
    assert result.next_cursor == "item-2"


def test_rss_connector_preserves_embedded_image_and_video_urls(monkeypatch, connector):
    feed = FEED_XML.replace(
        "<p>Hello <b>world</b>.</p>",
        '<p>Hello world.</p><img src="/media/cover.jpg"><video src="https://cdn.example/video.mp4"></video>',
    )
    monkeypatch.setattr(
        "backend.infrastructure.connectors.feed.rss.requests.get",
        lambda *_args, **_kwargs: DummyResponse(feed),
    )

    item = connector.fetch_new_items(since_cursor=None).items[0]

    assert item.raw_metadata["images"] == ["https://example.com/media/cover.jpg"]
    assert item.raw_metadata["videos"] == ["https://cdn.example/video.mp4"]


def test_rss_content_enrichment_fetches_article_and_media_despite_long_feed_summary(monkeypatch):
    feed_summary = (
        "This feed summary is deliberately longer than eighty characters so it already looks complete, "
        "but the linked article contains the authoritative full text."
    )
    article_body = (
        "The linked article contains the complete reporting, supporting evidence, and final conclusion. "
        "This authoritative page text must replace the feed summary during RSS content enrichment."
    )
    article_html = f"""
        <html><body><article>
          <p>{article_body}</p>
          <img src="/media/cover.jpg">
          <video src="https://cdn.example/video.mp4"></video>
        </article></body></html>
    """
    monkeypatch.setattr(artifacts.socket, "getaddrinfo", _public_address_record)
    monkeypatch.setattr(artifacts.requests, "get", lambda *_args, **_kwargs: DummyResponse(article_html))
    raw_item = RawItem(
        raw_item_id="rss-long-summary",
        connector_id="rss_test",
        source_platform="rss",
        source_type="article",
        source_url="https://example.com/posts/full-story",
        captured_at="2026-07-17T00:00:00Z",
        raw_text=feed_summary,
        raw_metadata={"title": "Full story"},
    )

    enriched = enrich_raw_item_content(raw_item)

    assert enriched.raw_text == article_body
    assert enriched.raw_metadata["enrichment_applied"] is True
    assert enriched.raw_metadata["images"] == ["https://example.com/media/cover.jpg"]
    assert enriched.raw_metadata["videos"] == ["https://cdn.example/video.mp4"]


def test_rss_content_enrichment_rejects_private_literal_without_request(monkeypatch):
    calls = []
    monkeypatch.setattr(artifacts.requests, "get", lambda *args, **kwargs: calls.append(args))
    raw_item = RawItem(
        raw_item_id="rss-private",
        connector_id="rss_test",
        source_platform="rss",
        source_type="article",
        source_url="http://127.0.0.1/internal",
        captured_at="2026-07-17T00:00:00Z",
        raw_text="Safe feed fallback",
    )

    enriched = enrich_raw_item_content(raw_item)

    assert enriched == raw_item
    assert calls == []


def test_rss_content_enrichment_revalidates_redirect_and_rejects_private_target(monkeypatch):
    redirect = DummyResponse(
        "",
        status_code=302,
        headers={"location": "http://192.168.1.10/internal"},
    )
    requested = []
    monkeypatch.setattr(artifacts.socket, "getaddrinfo", _public_address_record)
    monkeypatch.setattr(
        artifacts.requests,
        "get",
        lambda url, **_kwargs: requested.append(url) or redirect,
    )
    raw_item = RawItem(
        raw_item_id="rss-redirect-private",
        connector_id="rss_test",
        source_platform="rss",
        source_type="article",
        source_url="https://example.com/article",
        captured_at="2026-07-17T00:00:00Z",
        raw_text="Safe feed fallback",
    )

    enriched = enrich_raw_item_content(raw_item)

    assert enriched == raw_item
    assert requested == ["https://example.com/article"]
    assert redirect.closed is True


def test_rss_content_enrichment_rejects_oversized_article(monkeypatch):
    article_html = "<html><body><article>Authoritative article body " + ("x" * 100) + "</article></body></html>"
    response = DummyResponse(
        article_html,
        headers={"content-length": str(5 * 1024 * 1024 + 1)},
    )
    monkeypatch.setattr(artifacts.socket, "getaddrinfo", _public_address_record)
    monkeypatch.setattr(artifacts.requests, "get", lambda *_args, **_kwargs: response)
    raw_item = RawItem(
        raw_item_id="rss-oversized",
        connector_id="rss_test",
        source_platform="rss",
        source_type="article",
        source_url="https://example.com/article",
        captured_at="2026-07-17T00:00:00Z",
        raw_text="Safe feed fallback",
    )

    enriched = enrich_raw_item_content(raw_item)

    assert enriched == raw_item
    assert response.closed is True


def test_rss_connector_sends_user_agent(monkeypatch, connector):
    captured = {}

    def fake_get(url, timeout=10, headers=None):
        captured["headers"] = headers or {}
        return DummyResponse(FEED_XML)

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", fake_get)

    connector.fetch_new_items(since_cursor=None)

    assert captured["headers"]["User-Agent"].startswith("InfoOS/")


def test_rss_connector_respects_last_seen_cursor(monkeypatch, connector):
    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(FEED_XML))

    result = connector.fetch_new_items(since_cursor="item-1")

    assert [item.raw_item_id for item in result.items] == ["item-2"]
    assert result.next_cursor == "item-2"


def test_rss_connector_fetches_items_before_seen_cursor_for_newest_first_feeds(monkeypatch, connector):
    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(UPDATED_FEED_XML))

    result = connector.fetch_new_items(since_cursor="item-2")

    assert [item.raw_item_id for item in result.items] == ["item-3"]
    assert result.next_cursor == "item-3"


def test_rss_connector_fetches_atom_entries(monkeypatch, connector):
    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(ATOM_FEED_XML))

    result = connector.fetch_new_items(since_cursor=None)

    assert [item.raw_item_id for item in result.items] == [
        "tag:github.com,2008:Repository/1/v1.2.0",
        "tag:github.com,2008:Repository/1/v1.1.0",
    ]
    assert result.items[0].source_url == "https://github.com/example/project/releases/tag/v1.2.0"
    assert result.items[0].raw_author.name == "github-actions"
    assert result.items[0].raw_timestamps.published_at == "2026-06-26T01:02:03Z"
    assert "Release notes" in result.items[0].raw_text
    assert result.next_cursor == "tag:github.com,2008:Repository/1/v1.2.0"


def test_rss_connector_respects_max_items_per_run(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(UPDATED_FEED_XML))
    connector = RSSConnector(
        connector_id="rss_test",
        feed_url="https://example.com/feed.xml",
        data_dir=tmp_path / "data",
        max_items_per_run=1,
    )

    result = connector.fetch_new_items(since_cursor=None)

    assert [item.raw_item_id for item in result.items] == ["item-3"]
    assert result.visible_count == 3
    assert result.new_count == 1
    assert result.dedup_skipped_count == 2
    assert result.next_cursor == "item-3"


def test_raw_item_store_appends_jsonl_records(tmp_path, connector, monkeypatch):
    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(FEED_XML))
    store = RawItemStore(tmp_path / "data" / "raw")
    result = connector.fetch_new_items(since_cursor=None)

    output_path = store.append(connector.connector_id, result.items)

    assert output_path.exists()
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert '"raw_item_id":"item-1"' in lines[0]


def test_rss_connector_contract_reports_order_counts_cursor_and_empty_semantics(monkeypatch, connector):
    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda *_args, **_kwargs: DummyResponse(FEED_XML))

    result = connector.fetch_new_items(since_cursor="item-1")

    assert [item.raw_item_id for item in result.items] == ["item-2"]  # oldest-first feed order
    assert result.next_cursor == "item-2"
    assert result.visible_count == 2
    assert result.dedup_skipped_count == 1

    monkeypatch.setattr(
        "backend.infrastructure.connectors.feed.rss.requests.get",
        lambda *_args, **_kwargs: DummyResponse("<rss><channel></channel></rss>"),
    )
    empty = connector.fetch_new_items(since_cursor="item-2")

    assert empty.items == []
    assert empty.next_cursor == "item-2"
    assert empty.visible_count == 0
    assert empty.dedup_skipped_count == 0


def test_rss_connector_contract_preserves_requests_timeout_type(monkeypatch, connector):
    def timeout(*_args, **_kwargs):
        raise requests.Timeout("feed timed out")

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", timeout)

    with pytest.raises(requests.Timeout, match="feed timed out"):
        connector.fetch_new_items(since_cursor=None)
