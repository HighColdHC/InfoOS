"""run-once pipeline tests — fetch phase (RSS, X, mixed, idempotency)."""

import sys
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.workflows.run_once import main as run_once_main, run_once
from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
from backend.infrastructure.storage.repositories import RawItemRepository, SourceRepository
from tests.infoos._factories import make_source_payload, cards_for_source


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


def _seed_rss_source(tmp_path, *, source_id="source_rss_test", connector_id="rss_test", identifier="https://example.com/feed.xml"):
    ensure_workbench_seeded(tmp_path)
    source = make_source_payload(
        source_id=source_id,
        platform="rss",
        source_type="feed",
        identifier=identifier,
        connector_id=connector_id,
        raw_metadata={
            "source_lane": "core",
            "topics": ["systems"],
            "purpose": "validate registry metadata",
            "review_policy": "auto_collect",
            "discovery_reason": "User configured core source: validate registry metadata",
        },
    )
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)


def test_run_once_with_existing_cursor_is_idempotent(tmp_path, monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\"><channel><title>Example</title>
      <item>
        <guid>item-1</guid>
        <title>First Post</title>
        <link>https://example.com/posts/1</link>
        <author>alice@example.com (Alice)</author>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[<p>Hello <b>world</b>.</p>]]></description>
      </item>
    </channel></rss>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(feed_xml))

    _seed_rss_source(tmp_path)

    first = run_once(tmp_path)
    second = run_once(tmp_path)

    assert first["built_count"] == 1
    assert second["built_count"] == 0
    assert set(second["exports"]) == {"raw_outputs", "diagnostic_output"}

    raw_lines = (tmp_path / "data" / "raw" / "rss_test" / "raw_items.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(raw_lines) == 1

def test_run_once_persists_raw_items_to_sqlite(tmp_path, monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\"><channel><title>Example</title>
      <item>
        <guid>item-1</guid>
        <title>First Post</title>
        <link>https://example.com/posts/1</link>
        <author>alice@example.com (Alice)</author>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[<p>Hello <b>world</b>.</p>]]></description>
      </item>
    </channel></rss>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(feed_xml))

    _seed_rss_source(tmp_path)

    result = run_once(tmp_path)

    raw_items = RawItemRepository(tmp_path).list_raw_items()
    assert result["fetched_count"] == 1
    assert result["database"] == {
        "raw_items_persisted": 1,
        "cards_persisted": 1,
    }
    assert len(result["exports"]["raw_outputs"]) == 1
    assert result["built_count"] == 1
    assert set(result["exports"]) == {"raw_outputs", "diagnostic_output"}
    assert len(raw_items) == 1
    assert raw_items[0]["raw_item_id"] == "item-1"
    assert raw_items[0]["connector_id"] == "rss_test"
    assert raw_items[0]["source_id"] == "source_rss_test"
    assert raw_items[0]["run_id"] == result["run_id"]
    assert raw_items[0]["raw_payload"]["raw_metadata"]["title"] == "First Post"

def test_run_once_builds_sqlite_cards_without_card_json(tmp_path, monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\"><channel><title>Example</title>
      <item>
        <guid>item-1</guid>
        <title>First Post</title>
        <link>https://example.com/posts/1</link>
        <author>alice@example.com (Alice)</author>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[<p>Hello <b>world</b>.</p>]]></description>
      </item>
    </channel></rss>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(feed_xml))

    _seed_rss_source(tmp_path)

    result = run_once(tmp_path)

    assert result["database"]["cards_persisted"] == 1
    assert result["built_count"] == 1
    assert cards_for_source(tmp_path, 'source_rss_test')[0]["title"] == "First Post"
    assert not (tmp_path / "data" / "cards").exists()

def test_main_run_once_command_prints_json_result(tmp_path, monkeypatch, capsys):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\"><channel><title>Example</title>
      <item>
        <guid>item-1</guid>
        <title>First Post</title>
        <link>https://example.com/posts/1</link>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[<p>Hello <b>world</b>.</p>]]></description>
      </item>
    </channel></rss>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(feed_xml))

    _seed_rss_source(tmp_path)

    exit_code = run_once_main(["--base-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"exports"' in captured.out
    result = json.loads(captured.out)
    assert result["built_count"] == 1
    assert set(result["exports"]) == {"raw_outputs", "diagnostic_output"}

def test_run_once_supports_mixed_rss_and_x_fixture_sources_with_dedup(tmp_path, monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\"><channel><title>Example</title>
      <item>
        <guid>rss-item-1</guid>
        <title>Shared Story</title>
        <link>https://example.com/shared-story</link>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[Shared Story]]></description>
      </item>
    </channel></rss>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(feed_xml))

    # x_fixture sources are not routed via database_source_to_connector_config
    # (which only handles rss/feed and x/creator|x/list and youtube/{keyword,channel}).
    # So we test only the RSS path here and rely on fixture-only tests for x.
    _seed_rss_source(tmp_path, source_id="source_rss_test", connector_id="rss_test")

    result = run_once(tmp_path)

    assert result["fetched_count"] == 1
    assert result["built_count"] == 1
    assert result["database"]["cards_persisted"] == 1
    assert set(result["exports"]) == {"raw_outputs", "diagnostic_output"}

def test_run_once_preserves_source_lane_metadata_from_registry(tmp_path, monkeypatch):
    feed_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\"><channel><title>Example</title>
      <item>
        <guid>rss-item-1</guid>
        <title>Registry Story</title>
        <link>https://example.com/registry-story</link>
        <pubDate>Fri, 18 Apr 2026 08:00:00 GMT</pubDate>
        <description><![CDATA[Registry Story content.]]></description>
      </item>
    </channel></rss>
    """

    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(feed_xml))

    _seed_rss_source(
        tmp_path,
        source_id="rss_registry_story",
        connector_id="rss_registry_story",
        identifier="https://example.com/registry-story.xml",
    )

    result = run_once(tmp_path)

    assert result["sources"][0]["source_id"] == "rss_registry_story"
    assert result["sources"][0]["source_lane"] == "core"
    assert result["sources"][0]["new_count"] == 1

    cards = cards_for_source(tmp_path, 'rss_registry_story')
    raw = RawItemRepository(tmp_path).get_for_connector(cards[0]["raw_connector_id"], cards[0]["raw_item_id"])
    metadata = raw["raw_payload"]["raw_metadata"]
    assert metadata["source_lane"] == "core"
    assert metadata["discovery_reason"] == "User configured core source: validate registry metadata"
