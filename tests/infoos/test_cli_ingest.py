"""Diagnostic fetch and durable CARD_BUILD persistence tests."""

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.tasks.handlers.acquisition import handle_card_build
from backend.application.tasks.types import Task, TaskType
from backend.application.workflows.fetch import fetch
from backend.application.usecases.source_config import load_source_configs
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.repositories import CardRepository, RawItemRepository, SourceRepository

from tests.infoos._factories import card_count, feed_xml, DummyResponse, make_source_payload

def _run_card_build(base_dir, raw_item_id):
    result = handle_card_build(
        Task(task_id=f"build_{raw_item_id}", task_type=TaskType.CARD_BUILD,
             target_kind="raw_item", target_id=raw_item_id),
        base_dir,
    )
    assert result["status"] == "success"
    return result["artifacts"]


def test_load_source_configs_reads_db(tmp_path):
    from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
    from backend.infrastructure.storage.repositories import SourceRepository

    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source({
        "source_id": "rss_test",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "connector_id": "rss_test",
        "cadence_hours": 24.0,
        "enabled": True,
        "connector_status": "supported",
    })

    feeds = load_source_configs(tmp_path)

    assert len(feeds) == 1
    assert feeds[0]["connector_id"] == "rss_test"
    assert feeds[0]["type"] == "rss"
    assert feeds[0]["feed_url"] == "https://example.com/feed.xml"

def test_load_source_configs_skips_disabled_sources(tmp_path, monkeypatch):
    from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
    from backend.infrastructure.storage.repositories import SourceRepository

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="[]", stderr="", returncode=0)

    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "opencli")
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)

    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source({
        "source_id": "opencli_enabled",
        "platform": "x",
        "source_type": "creator",
        "identifier": "hackernews_top",
        "connector_id": "opencli_enabled",
        "cadence_hours": 1.0,
        "enabled": True,
        "connector_status": "supported",
    })
    SourceRepository(tmp_path).upsert_source({
        "source_id": "opencli_disabled",
        "platform": "x",
        "source_type": "creator",
        "identifier": "InfoOS",
        "connector_id": "opencli_disabled",
        "cadence_hours": 1.0,
        "enabled": False,
    })

    result = fetch(base_dir=tmp_path)

    assert result["fetched_count"] == 0
    assert calls == [[
        "opencli",
        "--profile",
        "infoos-fetch",
        "twitter",
        "tweets",
        "hackernews_top",
        "--limit",
        "50",
        "--format",
        "json",
        "--window",
        "background",
        "--site-session",
        "ephemeral",
        "--keep-tab",
        "false",
    ]]

def test_fetch_then_durable_card_build(tmp_path, monkeypatch):
    from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
    from backend.infrastructure.storage.repositories import SourceRepository

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(feed_xml()))

    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source({
        "source_id": "rss_test",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "connector_id": "rss_test",
        "cadence_hours": 24.0,
        "enabled": True,
        "connector_status": "supported",
    })

    fetch_result = fetch(base_dir=tmp_path)
    artifacts = _run_card_build(tmp_path, "item-1")

    assert len(fetch_result["exports"]["raw_outputs"]) == 1
    assert fetch_result["fetched_count"] == 1
    assert fetch_result["database"] == {"raw_items_persisted": 1}
    assert fetch_result["exports"] == {"raw_outputs": fetch_result["exports"]["raw_outputs"]}
    assert CardRepository(tmp_path).get_card(artifacts["card_id"]) is not None


def test_card_build_reads_sqlite_raw_items_when_jsonl_is_absent(tmp_path):
    SourceRepository(tmp_path).upsert_source(make_source_payload(source_id="source_rss_test"))
    RawItemRepository(tmp_path).upsert_raw_items(
        [
            RawItem(
                raw_item_id="db-item-1",
                connector_id="rss_test",
                source_platform="rss",
                source_type="article",
                source_url="https://example.com/posts/db-first",
                captured_at="2026-05-17T08:00:00Z",
                raw_text="Database-first card content with enough detail for a reusable intelligence card.",
                raw_metadata={
                    "title": "Database-first post",
                    "source_id": "source_rss_test",
                },
            )
        ],
        run_id="run_db_first",
    )

    assert not (tmp_path / "data" / "raw").exists()

    artifacts = _run_card_build(tmp_path, "db-item-1")

    card = CardRepository(tmp_path).get_card(artifacts["card_id"])
    assert card is not None
    assert card["title"] == "Database-first post"
    assert card["source_id"] == "source_rss_test"
    assert card["raw_item_id"] == "db-item-1"

def test_card_build_persists_generated_cards_to_sqlite(tmp_path):
    SourceRepository(tmp_path).upsert_source(make_source_payload(source_id="source_rss_test"))
    RawItemRepository(tmp_path).upsert_raw_items(
        [
            RawItem(
                raw_item_id="db-item-card",
                connector_id="rss_test",
                source_platform="rss",
                source_type="article",
                source_url="https://example.com/posts/sqlite-card",
                captured_at="2026-05-17T09:00:00Z",
                raw_text="SQLite card persistence content with enough detail for the workbench card table.",
                raw_metadata={
                    "title": "SQLite persisted card",
                    "source_id": "source_rss_test",
                },
            )
        ],
        run_id="run_sqlite_card",
    )

    artifacts = _run_card_build(tmp_path, "db-item-card")

    card = CardRepository(tmp_path).get_card(artifacts["card_id"])
    assert card is not None
    assert card_count(tmp_path) == 1
    assert card["title"] == "SQLite persisted card"
    assert card["source_id"] == "source_rss_test"
    assert card["raw_item_id"] == "db-item-card"

def test_card_build_omits_downstream_scoring_and_routing(tmp_path, monkeypatch):
    from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
    from backend.infrastructure.storage.repositories import SourceRepository

    monkeypatch.setattr("backend.infrastructure.connectors.feed.rss.requests.get", lambda url, timeout=10: DummyResponse(feed_xml()))

    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source({
        "source_id": "rss_test",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "connector_id": "rss_test",
        "cadence_hours": 24.0,
        "enabled": True,
        "connector_status": "supported",
    })

    fetch(base_dir=tmp_path)
    artifacts = _run_card_build(tmp_path, "item-1")

    card = CardRepository(tmp_path).get_card(artifacts["card_id"])
    assert card is not None
    card_payload = card["raw_payload"]

    assert card["card_id"] == artifacts["card_id"]
    assert not {"recommended_action", "routing", "scores", "topics"} & card_payload.keys()
    assert card["workflow_status"] == "processing"

def test_fetch_does_not_forward_loader_overrides_to_connector(tmp_path, monkeypatch):
    """Diagnostic loader payload cannot override the SQLite connector contract."""
    from backend.infrastructure.connectors.opencli import OpenCLIConnector
    from backend.infrastructure.storage.repositories import SourceRepository

    captured: list[OpenCLIConnector] = []

    real_init = OpenCLIConnector.__init__

    def spy_init(self, *args, **kwargs):
        captured.append(self)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(OpenCLIConnector, "__init__", spy_init)
    monkeypatch.setattr(
        "backend.infrastructure.connectors.opencli.subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout="[]", stderr="", returncode=0),
    )
    for source_id in ("opencli_ephemeral", "opencli_persistent", "opencli_defaulted"):
        SourceRepository(tmp_path).upsert_source(
            {
                "source_id": source_id,
                "platform": "x",
                "source_type": "creator",
                "identifier": source_id,
                "display_name": source_id,
                "cadence_hours": 1.0,
                "enabled": True,
                "connector_status": "supported",
                "connector_id": source_id,
            }
        )

    # The scheduler injects keep_alive into the source dict right before
    # the connector is built. CLI fetch() forwards whatever load_source_configs
    # returns, so we monkeypatch it to inject keep_alive into each entry.
    monkeypatch.setattr(
        "backend.application.workflows.fetch.load_source_configs",
        lambda base_dir: [
            {
                "source_id": "opencli_ephemeral",
                "enabled": True,
                "connector_id": "opencli_ephemeral",
                "type": "opencli",
                "command": ["twitter", "tweets", "elonmusk", "--limit", "1"],
                "executable": "opencli",
                "keep_alive": False,
            },
            {
                "source_id": "opencli_persistent",
                "enabled": True,
                "connector_id": "opencli_persistent",
                "type": "opencli",
                "command": ["youtube", "search", "infoos", "--limit", "1"],
                "executable": "opencli",
                "keep_alive": True,
            },
            {
                "source_id": "opencli_defaulted",
                "enabled": True,
                "connector_id": "opencli_defaulted",
                "type": "opencli",
                "command": ["hackernews", "top", "--limit", "1"],
                "executable": "opencli",
            },
        ],
    )

    fetch(base_dir=tmp_path)

    keep_alive_by_id = {c.connector_id: c.keep_alive for c in captured}
    assert keep_alive_by_id["opencli_ephemeral"] is True
    assert keep_alive_by_id["opencli_persistent"] is True
    assert keep_alive_by_id["opencli_defaulted"] is True
