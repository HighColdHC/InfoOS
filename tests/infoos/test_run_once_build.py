"""run-once pipeline tests — durable CARD_BUILD phase.

CARD_BUILD persists cards from SQLite raw items; context and media work
belong to subsequent durable tasks.
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.tasks.handlers.acquisition import handle_card_build
from backend.application.tasks.planner import raw_item_target_id
from backend.application.tasks.types import Task, TaskType
from backend.application.workflows.run_once import run_once
from tests.infoos._factories import make_source_payload, card_count
from backend.infrastructure.storage.repositories import CardRepository, RawItemRepository, RunRepository, SourceRepository


def _persist_and_build(base_dir, raw_items, *, run_id=None):
    for item in raw_items:
        source_id = item.raw_metadata.get("source_id")
        if source_id:
            SourceRepository(base_dir).upsert_source(make_source_payload(
                source_id=source_id, platform=item.source_platform,
                source_type="creator" if item.source_platform == "x" else "feed",
                connector_id=item.connector_id,
                identifier=source_id if item.source_platform == "x" else item.source_url,
            ))
    RawItemRepository(base_dir).upsert_raw_items(raw_items, run_id=run_id)
    cards = []
    for item in raw_items:
        result = handle_card_build(
            Task(task_id=f"build_{item.raw_item_id}", task_type=TaskType.CARD_BUILD,
                 target_kind="raw_item",
                 target_id=raw_item_target_id(item.connector_id, item.raw_item_id)),
            base_dir,
        )
        assert result["status"] == "success"
        card = CardRepository(base_dir).get_card(result["artifacts"]["card_id"])
        assert card is not None
        cards.append(card)
    return cards


def test_run_once_uses_acquisition_workflow_for_fetching(tmp_path, monkeypatch):
    from backend.infrastructure.storage.repositories import SourceRepository

    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_run_once",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "RSS run once",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_run_once",
        }
    )
    called: list[str] = []

    def fake_fetch_source(base_dir, *, source_id, task):
        called.append(source_id)
        return {
            "source_id": source_id,
            "connector_id": "rss_run_once",
            "run_id": task.artifacts_summary["run_id"],
            "visible_count": 0,
            "fetched_count": 0,
            "dedup_skipped_count": 0,
            "raw_item_ids": [],
            "card_build_task_ids": [],
            "cursor_before": None,
            "cursor_after": None,
        }

    monkeypatch.setattr("backend.application.workflows.acquisition.fetch_source", fake_fetch_source)

    result = run_once(tmp_path, source_filter="rss_run_once")

    assert called == ["rss_run_once"]
    assert result["fetched_count"] == 0


def test_x_context_enrichment_uses_stable_opencli_env(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        from types import SimpleNamespace

        calls.append(command)
        return SimpleNamespace(stdout="[]", stderr="", returncode=0)

    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "/stable/bin/opencli")
    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_run)

    from backend.application.enrichment.web_content import _run_opencli_json_command

    assert _run_opencli_json_command(["opencli", "twitter", "thread", "1", "--format", "json"]) == []
    assert calls == [[
        "/stable/bin/opencli", "--profile", "infoos-fetch",
        "twitter", "thread", "1", "--format", "json",
        "--site-session", "ephemeral", "--keep-tab", "false",
    ]]

def test_card_build_does_not_run_synchronous_x_context_enrichment(tmp_path, monkeypatch):
    from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps

    calls: list[str] = []

    def fake_enrich(card_id, raw_item, base_dir):
        calls.append(raw_item.raw_item_id)
        return []

    monkeypatch.setattr("backend.application.enrichment.x_blocks._shared.enrich_x_card_blocks", fake_enrich)
    monkeypatch.setattr(
        "backend.application.enrichment.media.download.download_x_card_media",
        lambda *args, **kwargs: calls.append("download_media") or [],
    )

    raw_items = [
        RawItem(
            raw_item_id=f"tweet-{idx}",
            connector_id="opencli_x_test",
            source_platform="x",
            source_type="social_post",
            source_url=f"https://x.com/test/status/{idx}",
            captured_at="2026-05-29T00:00:00Z",
            raw_text=f"tweet text {idx}",
            raw_author=RawAuthor(name="Test", handle="test"),
            raw_timestamps=RawTimestamps(published_at="2026-05-29T00:00:00Z"),
            raw_metadata={"title": f"tweet text {idx}"},
        )
        for idx in range(3)
    ]

    cards = _persist_and_build(tmp_path, raw_items)

    assert len(cards) == 3
    assert calls == []
    assert all("context_enrichment_deferred" not in card["raw_payload"] for card in cards)
    assert all("context_enrichment_complete" not in card["raw_payload"] for card in cards)


def test_card_build_does_not_fetch_generic_web_content_before_async_enrichment(tmp_path, monkeypatch):
    from backend.domain.models.raw_item import RawItem

    def unexpected_fetch(*args, **kwargs):
        raise AssertionError("CARD_BUILD must not fetch generic web content")

    monkeypatch.setattr(
        "backend.application.enrichment.web_content.enrich_raw_item_content", unexpected_fetch,
    )

    raw_items = [
        RawItem(
            raw_item_id="rss-1",
            connector_id="rss_ai",
            source_platform="rss",
            source_type="article",
            source_url="https://example.com/thin",
            captured_at="2026-05-29T00:00:00Z",
            raw_text="Thin title",
            raw_metadata={"source_id": "rss_ai", "title": "Thin title"},
        )
    ]

    cards = _persist_and_build(tmp_path, raw_items)

    assert len(cards) == 1
    assert cards[0]["content"] == "Thin title"


def test_card_build_does_not_enqueue_legacy_run_history_enrichment(tmp_path):
    from backend.domain.models.raw_item import RawItem

    raw_items = [
        RawItem(
            raw_item_id="rss-1",
            connector_id="rss_ai",
            source_platform="rss",
            source_type="article",
            source_url="https://example.com/one",
            captured_at="2026-05-29T00:00:00Z",
            raw_text="A card body",
            raw_metadata={"source_id": "rss_ai", "title": "A card body"},
        )
    ]

    cards = _persist_and_build(tmp_path, raw_items, run_id="fetch_legacy")

    assert len(cards) == 1
    assert [
        run for run in RunRepository(tmp_path).list_runs() if run.get("task_type") == "enrichment"
    ] == []


def test_x_cards_are_source_scoped_when_same_url_appears_in_multiple_sources(tmp_path):
    from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps

    raw_items = [
        RawItem(
            raw_item_id="tweet-1",
            connector_id=source_id,
            source_platform="x",
            source_type="social_post",
            source_url="https://x.com/elonmusk/status/1",
            captured_at="2026-05-29T00:00:00Z",
            raw_text="same tweet text",
            raw_author=RawAuthor(name="Elon Musk", handle="elonmusk"),
            raw_timestamps=RawTimestamps(published_at="2026-05-29T00:00:00Z"),
            raw_metadata={"source_id": source_id, "title": "same tweet text"},
        )
        for source_id in ("x_home_for_you", "x_user_elonmusk")
    ]

    cards = _persist_and_build(tmp_path, raw_items)
    persisted = [CardRepository(tmp_path).get_card(card["card_id"]) for card in cards]
    assert card_count(tmp_path) == 2

    assert len(cards) == 2
    assert len(persisted) == 2
    assert {card["source_id"] for card in persisted} == {"x_home_for_you", "x_user_elonmusk"}
    assert len({card["card_id"] for card in persisted}) == 2
