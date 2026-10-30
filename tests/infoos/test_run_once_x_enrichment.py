"""run-once pipeline tests — X (Twitter) enrichment (thread, article, repost, quote, t.co resolution)."""

import sys
import json
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.workflows.run_once import run_once
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
from backend.infrastructure.storage.repositories import CardRepository, RawItemRepository, SourceRepository
from tests.infoos._factories import make_supported_source_payload, opencli_command_startswith, cards_for_source

MINIMAL_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x01\x00\x01\x00\x00\xff\xd9"


def test_x_reply_keeps_only_one_parent_with_relation_metadata(tmp_path, monkeypatch):
    from backend.application.enrichment.x_blocks.reply import _enrich_reply_parent
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    CardRepository(tmp_path).upsert_card({
        "card_id": "card_reply",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/200",
        "title": "reply",
        "content": "回复正文",
        "captured_at": "2026-07-17T00:00:00Z",
        "processed_at": "2026-07-17T00:00:00Z",
    })
    raw = RawItem(
        raw_item_id="200",
        connector_id="x_reply",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/alice/status/200",
        captured_at="2026-07-17T00:00:00Z",
        raw_text="回复正文",
        raw_metadata={"opencli_record": {
            "in_reply_to_status_id_str": "100",
            "replied_to_tweet": {
                "id": "100",
                "text": "被回复的正文",
                "url": "https://x.com/bob/status/100",
                "media_urls": ["https://video.twimg.com/ext_tw_video/100/reply.mp4"],
                "replied_to_tweet": {"id": "99", "text": "不得继续展开"},
            },
        }},
    )

    block = _enrich_reply_parent(
        "card_reply",
        raw.source_url,
        CardContentBlockRepository(tmp_path),
        raw,
    )[0]

    assert block["body"] == "被回复的正文"
    assert block["raw_payload"]["content_role"] == "replied_to"
    assert block["raw_payload"]["relation_depth"] == 1
    assert block["raw_payload"]["relation_source_url"] == "https://x.com/bob/status/100"
    assert block["raw_payload"]["media_urls"] == [
        "https://video.twimg.com/ext_tw_video/100/reply.mp4"
    ]
    assert "不得继续展开" not in block["body"]


def test_x_reply_fetches_parent_when_acquisition_only_has_parent_identity(tmp_path, monkeypatch):
    from backend.application.enrichment.x_blocks.reply import _enrich_reply_parent
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    CardRepository(tmp_path).upsert_card({
        "card_id": "card_reply_identity_only",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/200",
        "title": "reply",
        "content": "reply body",
        "captured_at": "2026-07-17T00:00:00Z",
        "processed_at": "2026-07-17T00:00:00Z",
    })
    monkeypatch.setattr(
        "backend.application.enrichment.x_blocks.reply._run_opencli_json_command",
        lambda *_args, **_kwargs: [{
            "id": "100",
            "text": "parent body fetched from X",
            "url": "https://x.com/bob/status/100",
            "media_urls": ["https://pbs.twimg.com/media/reply.jpg"],
            "replied_to_tweet": {"id": "99", "text": "must not recurse"},
        }],
    )
    raw = RawItem(
        raw_item_id="200",
        connector_id="x_reply",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/alice/status/200",
        captured_at="2026-07-17T00:00:00Z",
        raw_text="reply body",
        raw_metadata={"opencli_record": {
            "in_reply_to_status_id_str": "100",
            "in_reply_to_screen_name": "bob",
        }},
    )

    block = _enrich_reply_parent(
        "card_reply_identity_only",
        raw.source_url,
        CardContentBlockRepository(tmp_path),
        raw,
    )[0]

    assert block["body"] == "parent body fetched from X"
    assert block["status"] == "enriched"
    assert block["source_url"] == "https://x.com/bob/status/100"
    assert block["raw_payload"] == {
        "content_role": "replied_to",
        "relation_depth": 1,
        "relation_source_url": "https://x.com/bob/status/100",
        "parent_tweet_id": "100",
        "asset_paths": [],
        "media_urls": ["https://pbs.twimg.com/media/reply.jpg"],
    }
    assert "must not recurse" not in block["body"]


def test_executor_x_content_enrich_parallel_steps_serialize_at_gateway(tmp_path, monkeypatch):
    """Regression: executor must not lend a parent-thread lease to X child workers."""
    from types import SimpleNamespace

    from backend.application.tasks.repository import TaskRepository
    from backend.application.tasks.types import Task, TaskType
    from backend.application.tasks.worker import run_due_tasks
    from backend.infrastructure.storage.repositories import TaskEventRepository

    card_id = "card_x_parallel_gateway"
    source_url = "https://x.com/example/status/123"
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="source_x_parallel",
        platform="x",
        source_type="creator",
        identifier="example",
        connector_id="source_x_parallel",
    ))
    CardRepository(tmp_path).upsert_card({
        "card_id": card_id,
        "raw_connector_id": "source_x_parallel",
        "raw_item_id": "123",
        "source_id": "source_x_parallel",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": source_url,
        "title": "post",
        "content": "plain post",
        "captured_at": "2026-07-15T00:00:00Z",
        "processed_at": "2026-07-15T00:00:00Z",
        "raw_payload": {},
    })
    RawItemRepository(tmp_path).upsert_raw_items([RawItem(
        raw_item_id="123",
        connector_id="source_x_parallel",
        source_platform="x",
        source_type="social_post",
        source_url=source_url,
        captured_at="2026-07-15T00:00:00Z",
        raw_text="plain post",
        raw_metadata={"source_id": "source_x_parallel"},
    )])
    task = Task(
        "content_enrich_x_parallel",
        TaskType.CONTENT_ENRICH,
        "card",
        card_id,
        artifacts_summary={"chain_id": "chain_x_parallel"},
    )
    TaskRepository.for_base_dir(tmp_path).enqueue(task)

    guard = threading.Lock()
    active = 0
    peak_active = 0
    command_threads: set[int] = set()
    commands: list[list[str]] = []

    def fake_opencli(command, **_kwargs):
        nonlocal active, peak_active
        with guard:
            active += 1
            peak_active = max(peak_active, active)
            command_threads.add(threading.get_ident())
            commands.append([str(part) for part in command])
        time.sleep(0.04)
        with guard:
            active -= 1
        stdout = "[]" if "thread" in command else "{}"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_opencli)
    started = time.perf_counter()
    result = run_due_tasks(tmp_path, task_types=[TaskType.CONTENT_ENRICH])
    elapsed = time.perf_counter() - started

    assert result["succeeded"] == 1
    assert elapsed < 2
    assert len(commands) == 1
    assert len(command_threads) == 1
    assert peak_active == 1
    lease_events = [
        event for event in TaskEventRepository(tmp_path).list_events(task_id=task.task_id)
        if event["event_type"] == "opencli_tab_lease_acquired"
    ]
    assert len(lease_events) == 1


def test_x_enrich_workers_keep_batch_state_without_sharing_lease(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from backend.application.enrichment.media import x_session
    from backend.application.enrichment.x_blocks import article, quote, repost, tco
    from backend.application.enrichment.x_blocks._shared import enrich_x_card_blocks
    from backend.application.tasks.types import Task, TaskType
    from backend.application.workflows.browser_acquisition_batch import (
        BrowserBatchState,
        active_browser_acquisition_batch,
        bind_browser_acquisition_batch,
    )
    from backend.infrastructure.opencli.tab_lease import opencli_tab_lease
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, TaskEventRepository

    source_id = "source_x_child_batch"
    card_id = "card_x_child_batch"
    source_url = "https://x.com/example/status/456"
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id=source_id,
        platform="x",
        source_type="creator",
        identifier="example",
        connector_id=source_id,
    ))
    CardRepository(tmp_path).upsert_card({
        "card_id": card_id,
        "source_id": source_id,
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": source_url,
        "title": "post",
        "content": "plain post",
        "captured_at": "2026-07-15T00:00:00Z",
        "processed_at": "2026-07-15T00:00:00Z",
        "raw_payload": {},
    })
    raw_item = RawItem(
        raw_item_id="456",
        connector_id=source_id,
        source_platform="x",
        source_type="social_post",
        source_url=source_url,
        captured_at="2026-07-15T00:00:00Z",
        raw_text="plain post",
        raw_metadata={"opencli_record": {"quoted_tweet": {"id": "455", "text": "parent"}}},
    )
    current_task = Task("enrich_current", TaskType.CONTENT_ENRICH, "card", card_id)
    other_task = Task("enrich_other", TaskType.CONTENT_ENRICH, "card", "card_other")
    ready = {
        "status": "ready",
        "profile": {"alias": "infoos-fetch", "bound": True, "binding_status": "ready"},
    }
    state = BrowserBatchState(tmp_path, [current_task, other_task], ready, False, [card_id, "card_other"])
    seen_states: list[BrowserBatchState | None] = []

    def observe(*_args):
        seen_states.append(active_browser_acquisition_batch())
        return []

    def observe_with_gateway(*_args):
        seen_states.append(active_browser_acquisition_batch())
        with opencli_tab_lease("twitter"):
            return []

    repairs: list[BrowserBatchState] = []
    commands = [
        SimpleNamespace(returncode=69, stdout="", stderr="BROWSER_CONNECT failed"),
        SimpleNamespace(returncode=0, stdout="[]", stderr=""),
    ]

    def quote_with_media(*_args):
        seen_states.append(active_browser_acquisition_batch())
        result = x_session.run_x_media_download_lifecycle(
            tweet_url=source_url,
            staging_root=tmp_path / "staging",
            command_runner=lambda _alias, _attempt_dir: commands.pop(0),
        )
        assert result.failure_reason is None
        return []

    monkeypatch.setattr(article, "_enrich_article", observe_with_gateway)
    monkeypatch.setattr(repost, "_enrich_repost", observe)
    monkeypatch.setattr(quote, "_enrich_quote", quote_with_media)
    monkeypatch.setattr(tco, "_enrich_tco_links", observe)
    monkeypatch.setattr(
        x_session,
        "prepare_opencli_browser_session",
        lambda **_kwargs: pytest.fail("child media must reuse the active batch"),
    )
    monkeypatch.setattr(
        x_session,
        "cleanup_headless_browser_session",
        lambda: pytest.fail("child media must not clean up the shared batch profile"),
    )
    monkeypatch.setattr(
        x_session,
        "repair_browser_acquisition_batch",
        lambda batch, **_kwargs: repairs.append(batch) or ready,
    )
    monkeypatch.setattr(x_session, "opencli_browser_lock", lambda: nullcontext())

    with bind_browser_acquisition_batch(state, task=current_task):
        enrich_x_card_blocks(card_id, raw_item, tmp_path)

    assert seen_states == [state] * 3
    assert repairs == [state]
    assert CardContentBlockRepository(tmp_path).list_for_card(card_id)
    current_events = TaskEventRepository(tmp_path).list_events(task_id=current_task.task_id)
    other_events = TaskEventRepository(tmp_path).list_events(task_id=other_task.task_id)
    assert [event["event_type"] for event in current_events] == [
        "opencli_tab_stale_close_completed",
        "opencli_tab_lease_acquired",
        "opencli_tab_close_completed",
    ]
    assert other_events == []


def _seed_x_list_source(tmp_path, *, identifier="9000000000000000001", connector_id="opencli_x_list"):
    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="x_list_test",
        platform="x",
        source_type="list",
        identifier=identifier,
        connector_id=connector_id,
    ))


def _run_async_content_enrichment_for_first_card(tmp_path) -> dict:
    from backend.application.enrichment.task_blocks import enrich_card_blocks_for_task

    cards = cards_for_source(tmp_path, 'x_list_test')
    assert len(cards) == 1
    enrich_card_blocks_for_task(cards[0]["card_id"], tmp_path)
    return cards[0]


def _x_opencli_fake(responses):
    from types import SimpleNamespace

    def fake_run(command, **kwargs):
        for suffix, (payload, stderr, returncode) in responses.items():
            if opencli_command_startswith(command, "opencli", "twitter", *suffix):
                stdout = payload if isinstance(payload, str) else json.dumps(payload)
                return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
        raise AssertionError(command)

    return fake_run


def test_async_content_enrichment_adds_x_thread_when_available(tmp_path, monkeypatch):
    fake_opencli_run = _x_opencli_fake({('list-tweets',): ([{'id': '9000000000000000002',
                          'url': 'https://x.com/example_thread/status/9000000000000000002',
                          'text': '短帖标题',
                          'created_at': '2026-04-24T09:00:00Z',
                          'author': {'name': 'example_thread', 'username': 'example_thread'}}],
                        '',
                        0),
     ('article', '9000000000000000002'): ('', 'Tweet 9000000000000000002 has no article content', 1),
     ('thread', '9000000000000000002'): ([{'id': '9000000000000000002',
                                           'author': 'example_thread',
                                           'text': '主帖：这是线程的开头。',
                                           'url': 'https://x.com/example_thread/status/9000000000000000002'},
                                          {'id': '9000000000000000003',
                                           'author': 'example_thread',
                                           'text': '回复一：这里补充了更完整的背景信息。',
                                           'url': 'https://x.com/example_thread/status/9000000000000000003'},
                                          {'id': '9000000000000000004',
                                           'author': 'example_thread',
                                           'text': '回复二：这里给出了结论和链接。',
                                           'url': 'https://x.com/example_thread/status/9000000000000000004'}],
                                         '',
                                         0)})

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli_run)
    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_opencli_run)

    _seed_x_list_source(tmp_path)

    run_once(tmp_path)
    card = _run_async_content_enrichment_for_first_card(tmp_path)

    cards = cards_for_source(tmp_path, 'x_list_test')
    # Phase 1: card content stays as original thin tweet text
    assert cards[0]["content"] == "短帖标题"

    # Replies are not imported as comments or thread context for a normal post.
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    blocks = CardContentBlockRepository(tmp_path).list_for_card(card["card_id"])
    thread_blocks = [b for b in blocks if b["block_kind"] == "thread"]
    assert thread_blocks == []

    # Original tweet is preserved separately
    original_blocks = [b for b in blocks if b["block_kind"] == "original_tweet"]
    assert len(original_blocks) == 1
    assert original_blocks[0]["body"] == "短帖标题"
    assert original_blocks[0]["layer"] == "original"
    assert original_blocks[0]["status"] == "preserved"

def test_async_content_enrichment_adds_x_article_when_available(tmp_path, monkeypatch):
    fake_opencli_run = _x_opencli_fake({('list-tweets',): ([{'id': '9000000000000000002',
                          'url': 'https://x.com/example_thread/status/9000000000000000002',
                          'text': '短帖标题',
                          'created_at': '2026-04-24T09:00:00Z',
                          'author': {'name': 'example_thread', 'username': 'example_thread'}}],
                        '',
                        0),
     ('article', '9000000000000000002'): ({'title': 'X Article title',
                                           'author': 'example_thread',
                                           'content': '这是 X Article 的长文正文，明显比单条 tweet '
                                                      '更完整，也比线程更适合作为正文来源。',
                                           'url': 'https://x.com/i/article/123'},
                                          '',
                                          0),
     ('thread', '9000000000000000002'): ([{'id': '9000000000000000002',
                                           'author': 'example_thread',
                                           'text': '回复帖，但不应出现在卡片正文中'}],
                                         '',
                                         0)})

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli_run)
    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_opencli_run)

    _seed_x_list_source(tmp_path)

    run_once(tmp_path)
    card = _run_async_content_enrichment_for_first_card(tmp_path)

    cards = cards_for_source(tmp_path, 'x_list_test')
    # Phase 1: card content stays as original thin tweet text
    assert cards[0]["content"] == "短帖标题"

    # Enriched article content lives in card_content_blocks
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    blocks = CardContentBlockRepository(tmp_path).list_for_card(card["card_id"])
    article_blocks = [b for b in blocks if b["block_kind"] == "x_article"]
    assert len(article_blocks) == 1
    assert "这是 X Article 的长文正文" in article_blocks[0]["body"]

    # Original tweet is preserved separately
    original_blocks = [b for b in blocks if b["block_kind"] == "original_tweet"]
    assert len(original_blocks) == 1
    assert original_blocks[0]["body"] == "短帖标题"
    assert original_blocks[0]["layer"] == "original"


# ====================================================================
# Phase 2: repost expansion, quote expansion, t.co resolution, summary gate
# ====================================================================

def test_async_content_enrichment_adds_x_repost_expansion(tmp_path, monkeypatch):
    fake_opencli_run = _x_opencli_fake({('list-tweets',): ([{'id': '9000000000000000002',
                          'url': 'https://x.com/example_thread/status/9000000000000000002',
                          'text': '短帖标题',
                          'created_at': '2026-04-24T09:00:00Z',
                          'author': {'name': 'example_thread', 'username': 'example_thread'},
                          'is_retweet': True}],
                        '',
                        0),
     ('article', '9000000000000000002'): ('', 'no article', 1),
     ('thread', '9000000000000000002'): ([], '', 0),
     ('retweet', '9000000000000000002'): ({'id': '9000000000000000006',
                                           'author': 'original_author',
                                           'text': '这是被转发的原始帖子内容。',
                                           'url': 'https://x.com/original_author/status/9000000000000000006'},
                                          '',
                                          0)})

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli_run)
    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_opencli_run)

    _seed_x_list_source(tmp_path)

    run_once(tmp_path)
    card = _run_async_content_enrichment_for_first_card(tmp_path)

    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    blocks = CardContentBlockRepository(tmp_path).list_for_card(card["card_id"])

    repost_blocks = [b for b in blocks if b["block_kind"] == "repost_original"]
    assert len(repost_blocks) == 1
    assert repost_blocks[0]["status"] == "enriched"
    assert "这是被转发的原始帖子内容。" in repost_blocks[0]["body"]
    assert repost_blocks[0]["layer"] == "context"
    assert repost_blocks[0]["position"] == 3

    original_blocks = [b for b in blocks if b["block_kind"] == "original_tweet"]
    assert original_blocks == []

def test_async_content_enrichment_adds_x_quote_expansion(tmp_path, monkeypatch):
    fake_opencli_run = _x_opencli_fake({('list-tweets',): ([{'id': '9000000000000000002',
                          'url': 'https://x.com/example_thread/status/9000000000000000002',
                          'text': '引用的短帖',
                          'created_at': '2026-04-24T09:00:00Z',
                          'author': {'name': 'example_thread', 'username': 'example_thread'},
                          'quoted_tweet': {'id': '9000000000000000005', 'text': '引用占位'}}],
                        '',
                        0),
     ('article', '9000000000000000002'): ('', 'no article', 1),
     ('thread', '9000000000000000002'): ([], '', 0),
     ('retweet', '9000000000000000002'): ({}, '', 0),
     ('quote', '9000000000000000002'): ({'id': '9000000000000000005',
                                         'author': 'quoted_author',
                                         'text': '这是被引用的帖子内容，提供了额外的上下文。',
                                         'url': 'https://x.com/quoted_author/status/9000000000000000005'},
                                        '',
                                        0)})

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli_run)
    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_opencli_run)

    _seed_x_list_source(tmp_path)

    run_once(tmp_path)
    card = _run_async_content_enrichment_for_first_card(tmp_path)

    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    blocks = CardContentBlockRepository(tmp_path).list_for_card(card["card_id"])

    quote_blocks = [b for b in blocks if b["block_kind"] == "quote"]
    assert len(quote_blocks) == 1
    assert quote_blocks[0]["status"] == "enriched"
    assert "这是被引用的帖子内容" in quote_blocks[0]["body"]
    assert quote_blocks[0]["layer"] == "context"
    assert quote_blocks[0]["position"] == 4

    original_blocks = [b for b in blocks if b["block_kind"] == "original_tweet"]
    assert len(original_blocks) == 1
    assert original_blocks[0]["body"] == "引用的短帖"


def test_upsert_block_passes_raw_payload_through(tmp_path):
    from backend.application.enrichment.x_blocks._helpers import _upsert_block
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository

    CardRepository(tmp_path).upsert_card({
        "card_id": "card_abc",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/1",
        "title": "",
        "content": "main body",
        "summary": "main body",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-04-19T10:00:00Z",
        "captured_at": "2026-04-19T10:00:00Z",
        "processed_at": "2026-04-19T10:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })
    repo = CardContentBlockRepository(tmp_path)
    _upsert_block(
        repo,
        card_id="card_abc",
        position=0,
        layer="context",
        block_kind="repost_original",
        status="enriched",
        body="text body",
        source_url="https://x.com/foo/status/1",
        provenance="Repost original",
        raw_payload={"quoted_author": {"name": "Bob", "handle": "bob"}},
    )
    blocks = repo.list_for_card("card_abc")
    assert len(blocks) == 1
    assert blocks[0]["raw_payload"]["quoted_author"] == {"name": "Bob", "handle": "bob"}


def test_download_x_card_media_names_assets_with_sub_kind(tmp_path, monkeypatch):
    from backend.application.enrichment.media import download

    assets_dir = tmp_path / "data" / "assets" / "card_xyz" / "tweets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "photo_quote_1.jpg").write_bytes(MINIMAL_JPEG_BYTES)

    # Bootstrap the card row so the FK on card_assets.card_id is satisfied.
    from backend.infrastructure.storage.repositories import CardRepository
    CardRepository(tmp_path).upsert_card({
        "card_id": "card_xyz",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/1",
        "title": "",
        "content": "main body",
        "summary": "main body",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-04-19T10:00:00Z",
        "captured_at": "2026-04-19T10:00:00Z",
        "processed_at": "2026-04-19T10:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })

    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    blocks = download._create_media_blocks(
        "card_xyz",
        "https://x.com/quoted/status/1",
        download._list_asset_files(assets_dir.parent),
        CardAssetRepository(tmp_path),
        CardContentBlockRepository(tmp_path),
        sub_kind="quote",
    )
    assert len(blocks) == 1
    assert blocks[0]["block_id"] == "block_card_xyz_image_quote_photo_quote_1"

    assets = CardAssetRepository(tmp_path).list_for_card("card_xyz")
    assert len(assets) == 1
    assert assets[0]["asset_id"] == "asset_card_xyz_quote_photo_quote_1"


def test_download_x_card_media_refreshes_existing_sub_kind_media_payload(tmp_path, monkeypatch):
    from backend.application.enrichment.media import download
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository

    assets_dir = tmp_path / "data" / "assets" / "card_xyz" / "tweets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "photo_quote_1.jpg").write_bytes(MINIMAL_JPEG_BYTES)

    CardRepository(tmp_path).upsert_card({
        "card_id": "card_xyz",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/1",
        "title": "",
        "content": "main body",
        "summary": "main body",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-04-19T10:00:00Z",
        "captured_at": "2026-04-19T10:00:00Z",
        "processed_at": "2026-04-19T10:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })
    block_repo = CardContentBlockRepository(tmp_path)
    block_repo.upsert_block({
        "block_id": "block_card_xyz_image_quote_photo_quote_1",
        "card_id": "card_xyz",
        "position": 1,
        "layer": "media",
        "block_kind": "image",
        "status": "downloaded",
        "body": "card_xyz/tweets/photo_quote_1.jpg",
        "source_url": "https://x.com/quoted/status/1",
        "provenance": "X image",
        "raw_payload": {},
    })

    from backend.infrastructure.storage.repositories import CardAssetRepository
    blocks = download._create_media_blocks(
        "card_xyz",
        "https://x.com/quoted/status/1",
        download._list_asset_files(assets_dir.parent),
        CardAssetRepository(tmp_path),
        block_repo,
        sub_kind="quote",
    )

    assert blocks[0]["raw_payload"] == {
        "embedded_in_block_id": "block_card_xyz_quote",
        "embedded_in_block_kind": "quote",
        "sub_kind": "quote",
    }
    stored = block_repo.list_for_card("card_xyz")
    assert stored[0]["raw_payload"]["embedded_in_block_id"] == "block_card_xyz_quote"


def test_quote_block_records_author_metadata_and_media_candidates(tmp_path, monkeypatch):
    """Quote payload with author + media[] should produce a quote block
    with raw_payload.quoted_author, raw_payload.quoted_at, and durable media candidates."""
    import json
    from types import SimpleNamespace
    from backend.application.enrichment.x_blocks import quote as quote_mod
    from backend.application.enrichment.media import x_session
    from contextlib import nullcontext
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    def fake_opencli(command, **kwargs):
        if opencli_command_startswith(command, "opencli", "twitter", "quote", "111"):
            payload = {
                "id": "222",
                "author": {"name": "Bob", "username": "bob", "url": "https://x.com/bob"},
                "text": "the quoted body",
                "url": "https://x.com/bob/status/222",
                "media": ["https://pbs.twimg.com/media/abc.jpg"],
                "media_kind": "video",
                "metrics": {"reply_count": 4359, "retweet_count": 10000, "like_count": 72000},
                "created_at": "2026-04-19T10:00:00Z",
            }
            return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
        if opencli_command_startswith(command, "opencli", "twitter", "retweet", "111"):
            return SimpleNamespace(stdout="{}", stderr="", returncode=0)
        if opencli_command_startswith(command, "opencli", "--profile", "infoos-fetch", "twitter", "download"):
            # Create the expected file inside the assets dir
            out_dir = Path(kwargs.get("args", ["", "-o"])[0]) if False else None
            # The download command passes --output <assets_dir>; find it in command list.
            try:
                idx = command.index("--output")
                out_dir = Path(command[idx + 1])
            except ValueError:
                pass
            if out_dir:
                tweets_dir = out_dir / "tweets"
                tweets_dir.mkdir(parents=True, exist_ok=True)
                (tweets_dir / "photo.jpg").write_bytes(MINIMAL_JPEG_BYTES)
            return SimpleNamespace(stdout="[]", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli)
    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_opencli)
    monkeypatch.setattr("backend.application.enrichment.media.download.subprocess.run", fake_opencli)
    monkeypatch.setattr(x_session, "prepare_opencli_browser_session", lambda **_kwargs: {
        "status": "ready",
        "profile": {"alias": "infoos-fetch", "bound": True, "binding_status": "ready"},
    })
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: None)
    monkeypatch.setattr(x_session, "opencli_browser_lock", nullcontext)
    monkeypatch.setattr(quote_mod.subprocess if hasattr(quote_mod, "subprocess") else quote_mod, "_run_opencli_json_command", None)
    # Replace _run_opencli_json_command with a stub that calls our fake runner
    def fake_run_opencli_json_command(cmd, timeout=15):
        result = fake_opencli(cmd)
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
    monkeypatch.setattr("backend.application.enrichment.web_content._run_opencli_json_command", fake_run_opencli_json_command)
    monkeypatch.setattr(quote_mod, "_run_opencli_json_command", fake_run_opencli_json_command)

    # Set up a card first
    CardRepository(tmp_path).upsert_card({
        "card_id": "card_q1",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/111",
        "source_author": "Alice",
        "title": "",
        "content": "main body",
        "summary": "main body",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-04-19T10:00:00Z",
        "captured_at": "2026-04-19T10:00:00Z",
        "processed_at": "2026-04-19T10:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })

    raw_item = RawItem(
        raw_item_id="ri-1",
        connector_id="x_test",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/alice/status/111",
        captured_at="2026-04-19T10:00:00Z",
        raw_text="main body",
        raw_metadata={},
    )

    blocks = quote_mod._enrich_quote(
        card_id="card_q1",
        tweet_id="111",
        source_url="https://x.com/alice/status/111",
        repo=CardContentBlockRepository(tmp_path),
        raw_item=raw_item,
    )

    assert len(blocks) >= 1
    main_block = blocks[0]
    assert main_block["block_kind"] == "quote"
    assert main_block["status"] == "enriched"
    assert main_block["raw_payload"]["quoted_author"]["handle"] == "bob"
    assert main_block["raw_payload"]["quoted_at"] == "2026-04-19T10:00:00Z"
    assert main_block["raw_payload"]["asset_paths"] == []
    assert main_block["raw_payload"]["media_urls"] == ["https://pbs.twimg.com/media/abc.jpg"]
    assert main_block["raw_payload"]["media_kind"] == "video"
    assert main_block["raw_payload"]["metrics"] == {
        "reply_count": 4359,
        "retweet_count": 10000,
        "like_count": 72000,
    }

    all_blocks = CardContentBlockRepository(tmp_path).list_for_card("card_q1")
    image_blocks = [b for b in all_blocks if b["block_kind"] == "image"]
    assert image_blocks == []


def test_quote_block_fallback_metadata_records_author_and_media(tmp_path, monkeypatch):
    from backend.application.enrichment.x_blocks import quote as quote_mod
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository

    monkeypatch.setattr(quote_mod, "_run_opencli_json_command", lambda *args, **kwargs: None)

    CardRepository(tmp_path).upsert_card({
        "card_id": "card_q_meta",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/111",
        "source_author": "Alice",
        "title": "",
        "content": "main body",
        "summary": "main body",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-04-19T10:00:00Z",
        "captured_at": "2026-04-19T10:00:00Z",
        "processed_at": "2026-04-19T10:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })
    raw_item = RawItem(
        raw_item_id="111",
        connector_id="x_test",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/alice/status/111",
        captured_at="2026-04-19T10:00:00Z",
        raw_text="main body",
        raw_metadata={
            "opencli_record": {
                "quoted_tweet": {
                    "author": "example_quote",
                    "name": "Example Quote Author",
                    "created_at": "Sun Jun 21 01:33:01 +0000 2026",
                    "text": "quoted body from metadata",
                    "url": "https://x.com/example_quote/status/222",
                    "media_urls": ["https://video.twimg.com/amplify_video/222/vid/avc1/example.mp4"],
                    "likes": 72000,
                    "retweets": 10000,
                    "replies": 4359,
                    "views": 10519000,
                }
            }
        },
    )

    blocks = quote_mod._enrich_quote(
        card_id="card_q_meta",
        tweet_id="111",
        source_url="https://x.com/alice/status/111",
        repo=CardContentBlockRepository(tmp_path),
        raw_item=raw_item,
    )

    assert blocks[0]["status"] == "enriched"
    assert blocks[0]["raw_payload"]["quoted_author"] == {
        "name": "Example Quote Author",
        "handle": "example_quote",
        "url": "https://x.com/example_quote",
    }
    assert blocks[0]["raw_payload"]["quoted_at"] == "Sun Jun 21 01:33:01 +0000 2026"
    assert blocks[0]["raw_payload"]["media_kind"] == "video"
    assert blocks[0]["raw_payload"]["metrics"] == {
        "reply_count": 4359,
        "retweet_count": 10000,
        "like_count": 72000,
        "view_count": 10519000,
    }
    assert blocks[0]["raw_payload"]["asset_paths"] == []
    assert blocks[0]["raw_payload"]["relation_source_url"] == "https://x.com/example_quote/status/222"
    assert blocks[0]["raw_payload"]["media_urls"] == ["https://video.twimg.com/amplify_video/222/vid/avc1/example.mp4"]


def test_repost_block_records_author_metadata_and_media_candidates(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from backend.application.enrichment.x_blocks import repost as repost_mod
    from backend.application.enrichment.media import x_session
    from contextlib import nullcontext
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardAssetRepository, CardRepository

    def fake_opencli(command, **kwargs):
        if opencli_command_startswith(command, "opencli", "twitter", "retweet", "111"):
            payload = {
                "id": "333",
                "author": {"name": "Carol", "username": "carol", "url": "https://x.com/carol"},
                "text": "the reposted body",
                "url": "https://x.com/carol/status/333",
                "media": ["https://pbs.twimg.com/media/xyz.jpg"],
                "created_at": "2026-04-19T11:00:00Z",
            }
            return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
        if opencli_command_startswith(command, "opencli", "--profile", "infoos-fetch", "twitter", "download"):
            try:
                idx = command.index("--output")
                out_dir = Path(command[idx + 1])
            except ValueError:
                return SimpleNamespace(stdout="", stderr="", returncode=1)
            tweets_dir = out_dir / "tweets"
            tweets_dir.mkdir(parents=True, exist_ok=True)
            (tweets_dir / "rt.jpg").write_bytes(MINIMAL_JPEG_BYTES)
            return SimpleNamespace(stdout="[]", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli)
    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_opencli)
    monkeypatch.setattr("backend.application.enrichment.media.download.subprocess.run", fake_opencli)
    monkeypatch.setattr(x_session, "prepare_opencli_browser_session", lambda **_kwargs: {
        "status": "ready",
        "profile": {"alias": "infoos-fetch", "bound": True, "binding_status": "ready"},
    })
    monkeypatch.setattr(x_session, "cleanup_headless_browser_session", lambda: None)
    monkeypatch.setattr(x_session, "opencli_browser_lock", nullcontext)

    def fake_run_opencli_json_command(cmd, timeout=15):
        result = fake_opencli(cmd)
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
    monkeypatch.setattr("backend.application.enrichment.web_content._run_opencli_json_command", fake_run_opencli_json_command)
    monkeypatch.setattr(repost_mod, "_run_opencli_json_command", fake_run_opencli_json_command)

    CardRepository(tmp_path).upsert_card({
        "card_id": "card_r1",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/111",
        "source_author": "Alice",
        "title": "",
        "content": "RT @carol: the reposted body",
        "summary": "RT @carol: the reposted body",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-04-19T11:00:00Z",
        "captured_at": "2026-04-19T11:00:00Z",
        "processed_at": "2026-04-19T11:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })

    raw_item = RawItem(
        raw_item_id="ri-2",
        connector_id="x_test",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/alice/status/111",
        captured_at="2026-04-19T11:00:00Z",
        raw_text="RT @carol: the reposted body",
        raw_metadata={},
    )

    blocks = repost_mod._enrich_repost(
        card_id="card_r1",
        tweet_id="111",
        source_url="https://x.com/alice/status/111",
        repo=CardContentBlockRepository(tmp_path),
        raw_item=raw_item,
    )

    assert len(blocks) == 1
    main_block = blocks[0]
    assert main_block["block_kind"] == "repost_original"
    assert main_block["status"] == "enriched"
    assert main_block["raw_payload"]["quoted_author"]["handle"] == "carol"
    assert main_block["raw_payload"]["asset_paths"] == []
    assert main_block["raw_payload"]["media_urls"] == ["https://pbs.twimg.com/media/xyz.jpg"]


def test_repost_block_fallback_metadata_records_author_and_media(tmp_path, monkeypatch):
    from backend.application.enrichment.x_blocks import repost as repost_mod
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository

    monkeypatch.setattr(repost_mod, "_run_opencli_json_command", lambda *args, **kwargs: None)

    CardRepository(tmp_path).upsert_card({
        "card_id": "card_r_meta",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/111",
        "source_author": "Alice",
        "title": "",
        "content": "RT @carol: reposted body from metadata",
        "summary": "RT @carol: reposted body from metadata",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-04-19T10:00:00Z",
        "captured_at": "2026-04-19T10:00:00Z",
        "processed_at": "2026-04-19T10:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })
    raw_item = RawItem(
        raw_item_id="111",
        connector_id="x_test",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/alice/status/111",
        captured_at="2026-04-19T10:00:00Z",
        raw_text="RT @carol: reposted body from metadata",
        raw_metadata={
            "opencli_record": {
                "retweeted_tweet": {
                    "author": "carol",
                    "name": "Carol",
                    "created_at": "2026-04-19T11:00:00Z",
                    "text": "reposted body from metadata",
                    "url": "https://x.com/carol/status/333",
                    "media_urls": ["https://pbs.twimg.com/media/example.jpg"],
                    "likes": 10,
                    "retweets": 2,
                    "replies": 1,
                }
            }
        },
    )

    blocks = repost_mod._enrich_repost(
        card_id="card_r_meta",
        tweet_id="111",
        source_url="https://x.com/alice/status/111",
        repo=CardContentBlockRepository(tmp_path),
        raw_item=raw_item,
    )

    assert blocks[0]["status"] == "enriched"
    assert blocks[0]["raw_payload"]["quoted_author"] == {
        "name": "Carol",
        "handle": "carol",
        "url": "https://x.com/carol",
    }
    assert blocks[0]["raw_payload"]["quoted_at"] == "2026-04-19T11:00:00Z"
    assert blocks[0]["raw_payload"]["media_kind"] == "image"
    assert blocks[0]["raw_payload"]["metrics"] == {
        "reply_count": 1,
        "retweet_count": 2,
        "like_count": 10,
    }
    assert blocks[0]["raw_payload"]["asset_paths"] == []
    assert blocks[0]["raw_payload"]["relation_source_url"] == "https://x.com/carol/status/333"
    assert blocks[0]["raw_payload"]["media_urls"] == ["https://pbs.twimg.com/media/example.jpg"]


def test_repost_block_public_fallback_uses_thread_payload_for_full_text_and_metrics(tmp_path, monkeypatch):
    import backend.application.enrichment.x_blocks.repost as repost_mod
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository
    from backend.domain.models.raw_item import RawItem

    def fake_run_opencli_json_command(cmd, timeout=15):
        if opencli_command_startswith(cmd, "opencli", "twitter", "retweet", "111"):
            return None
        if opencli_command_startswith(cmd, "opencli", "twitter", "thread", "https://x.com/example_repost/status/333"):
            return [{
                "id": "333",
                "author": "example_repost",
                "text": "Full expanded repost text after Show more",
                "url": "https://x.com/example_repost/status/333",
                "created_at": "Mon Jun 22 17:24:22 +0000 2026",
                "likes": 1571,
                "retweets": 401,
                "media_urls": ["https://pbs.twimg.com/media/full.jpg"],
                "quoted_tweet": {
                    "author": "example_nested",
                    "text": "Nested quote from thread payload",
                    "url": "https://x.com/example_nested/status/222",
                },
            }]
        raise AssertionError(cmd)

    monkeypatch.setattr(repost_mod, "_run_opencli_json_command", fake_run_opencli_json_command)
    monkeypatch.setattr(
        repost_mod,
        "_fetch_public_x_repost",
        lambda source_url: {
            "author": "example_repost",
            "name": "Example Repost",
            "url": "https://x.com/example_repost/status/333",
            "text": "Truncated public text...",
            "media_urls": ["https://pbs.twimg.com/media/public.jpg"],
            "nested_quote": None,
        },
    )

    CardRepository(tmp_path).upsert_card({
        "card_id": "card_rt_thread",
        "source_id": None,
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/example_sharer/status/111",
        "title": "",
        "content": "RT @example_repost: Truncated public text...",
        "summary": "RT @example_repost: Truncated public text...",
        "captured_at": "2026-06-23T10:00:00Z",
        "processed_at": "2026-06-23T10:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })
    raw_item = RawItem(
        raw_item_id="111",
        connector_id="x_user_example_sharer",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/example_sharer/status/111",
        captured_at="2026-06-23T10:00:00Z",
        raw_text="RT @example_repost: Truncated public text...",
        raw_metadata={},
    )

    blocks = repost_mod._enrich_repost(
        card_id="card_rt_thread",
        tweet_id="111",
        source_url="https://x.com/example_sharer/status/111",
        repo=CardContentBlockRepository(tmp_path),
        raw_item=raw_item,
    )

    assert blocks[0]["status"] == "enriched"
    assert blocks[0]["body"] == "Full expanded repost text after Show more"
    assert blocks[0]["provenance"] == "Repost original (from OpenCLI thread)"
    assert blocks[0]["raw_payload"]["quoted_author"] == {
        "name": "example_repost",
        "handle": "example_repost",
        "url": "https://x.com/example_repost",
    }
    assert blocks[0]["raw_payload"]["metrics"] == {
        "retweet_count": 401,
        "like_count": 1571,
    }
    assert blocks[0]["raw_payload"]["media_urls"] == ["https://pbs.twimg.com/media/public.jpg"]
    assert blocks[0]["raw_payload"]["nested_quote"] == {
        "author": {"name": "example_nested", "handle": "example_nested", "url": "https://x.com/example_nested"},
        "text": "Nested quote from thread payload",
        "url": "https://x.com/example_nested/status/222",
    }


def test_repost_block_parses_rt_text_when_original_payload_missing(tmp_path, monkeypatch):
    import backend.application.enrichment.x_blocks.repost as repost_mod
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository
    from backend.domain.models.raw_item import RawItem

    monkeypatch.setattr(repost_mod, "_run_opencli_json_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(repost_mod, "_fetch_public_x_repost", lambda *args, **kwargs: None)
    _seed_x_list_source(tmp_path)
    CardRepository(tmp_path).upsert_card({
        "card_id": "card_rt_text",
        "source_id": "x_list_test",
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/example_sharer/status/111",
        "title": "",
        "content": "RT @example_origin: Original repost body",
        "summary": "RT @example_origin: Original repost body",
        "captured_at": "2026-04-19T10:00:00Z",
        "processed_at": "2026-04-19T10:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })
    raw_item = RawItem(
        raw_item_id="111",
        connector_id="x_user_example_sharer",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/example_sharer/status/111",
        captured_at="2026-04-19T10:00:00Z",
        raw_text="RT @example_origin: Original repost body",
        raw_metadata={
            "opencli_record": {
                "author": "example_sharer",
                "name": "Example Sharer",
                "is_retweet": True,
            }
        },
    )

    blocks = repost_mod._enrich_repost(
        card_id="card_rt_text",
        tweet_id="111",
        source_url="https://x.com/example_sharer/status/111",
        repo=CardContentBlockRepository(tmp_path),
        raw_item=raw_item,
    )

    assert blocks[0]["status"] == "enriched"
    assert blocks[0]["body"] == "Original repost body"
    assert blocks[0]["source_url"] == "https://x.com/example_origin"
    assert blocks[0]["raw_payload"]["quoted_author"] == {
        "name": "example_origin",
        "handle": "example_origin",
        "url": "https://x.com/example_origin",
    }
    assert blocks[0]["raw_payload"]["reposted_by"] == {
        "name": "Example Sharer",
        "handle": "example_sharer",
        "url": "https://x.com/example_sharer",
    }


def test_public_repost_parser_returns_original_status_and_media(monkeypatch):
    import io
    import backend.application.enrichment.x_blocks.repost as repost_mod

    html = """
    <html><body>
      <div>Example Sharer reposted</div>
      <article data-tweet-id="9000000000000000009">
        <a href="/example_repost"><span>Example Repost</span></a>
        <div dir="auto">The new /plan command in Example Product is ready for testing</div>
        <img src="https://pbs.twimg.com/media/synthetic-main-image.jpg" />
        <a data-href="/example_nested/status/9000000000000000008">
          <a href="/example_nested"><span>Example Product</span></a>
          <div dir="auto">Introducing /plan in Example Product.</div>
          <video src="https://video.twimg.com/amplify_video/9000000000000000007/vid/avc1/1434x1080/synthetic-quote-video.mp4?tag=28"></video>
        </a>
      </article>
    </body></html>
    """

    class FakeResponse:
        def read(self):
            return html.encode("utf-8")

    monkeypatch.setattr(repost_mod, "urlopen", lambda *args, **kwargs: FakeResponse())

    payload = repost_mod._fetch_public_x_repost("https://x.com/example_sharer/status/9000000000000000015")

    assert payload is not None
    assert payload["url"] == "https://x.com/example_repost/status/9000000000000000009"
    assert payload["text"] == "The new /plan command in Example Product is ready for testing"
    assert payload["media_urls"] == ["https://pbs.twimg.com/media/synthetic-main-image.jpg"]
    assert payload["nested_quote"]["url"] == "https://x.com/example_nested/status/9000000000000000008"


def test_public_repost_parser_keeps_main_video_variants_separate_from_quote(monkeypatch):
    import backend.application.enrichment.x_blocks.repost as repost_mod

    html = """
    <html><body>
      <div>Example Sharer reposted</div>
      <article data-tweet-id="9000000000000000013">
        <a href="/example_media"><span>example_media</span></a>
        <div dir="auto">A synthetic video post demonstrates separate media variants.</div>
        <video src="https://video.twimg.com/amplify_video/9000000000000000012/pl/synthetic-main-playlist.m3u8?tag=28"></video>
        <a data-href="/example_media/status/9000000000000000011">
          <a href="/example_media"><span>example_media</span></a>
          <div dir="auto">Nested quote text</div>
          <video src="https://video.twimg.com/amplify_video/9000000000000000010/pl/synthetic-quote-playlist.m3u8?tag=28"></video>
        </a>
        <script>
          "https://video.twimg.com/amplify_video/9000000000000000012/vid/avc1/294x270/low.mp4?tag=28",
          "https://video.twimg.com/amplify_video/9000000000000000012/vid/avc1/718x656/high.mp4?tag=28",
          "https://video.twimg.com/amplify_video/9000000000000000010/vid/avc1/608x562/quote.mp4?tag=28"
        </script>
      </article>
    </body></html>
    """

    class FakeResponse:
        def read(self):
            return html.encode("utf-8")

    monkeypatch.setattr(repost_mod, "urlopen", lambda *args, **kwargs: FakeResponse())

    payload = repost_mod._fetch_public_x_repost("https://x.com/example_sharer/status/9000000000000000014")

    assert payload is not None
    assert payload["media_urls"] == [
        "https://video.twimg.com/amplify_video/9000000000000000012/vid/avc1/718x656/high.mp4?tag=28"
    ]


def test_download_x_card_media_ignores_html_saved_with_video_extension(tmp_path, monkeypatch):
    from backend.application.enrichment.media import download
    from backend.infrastructure.storage.repositories import CardRepository

    CardRepository(tmp_path).upsert_card({
        "card_id": "card_bad_video",
        "source_id": None,
        "source_platform": "x",
        "source_type": "social_post",
        "source_url": "https://x.com/alice/status/1",
        "source_author": "alice",
        "title": "Bad video",
        "content": "Bad video",
        "summary": "",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-04-19T10:00:00Z",
        "captured_at": "2026-04-19T10:00:00Z",
        "processed_at": "2026-04-19T10:00:00Z",
        "workflow_status": "export_ready",
        "raw_payload": {},
    })
    tweets_dir = tmp_path / "data" / "assets" / "card_bad_video" / "tweets"
    tweets_dir.mkdir(parents=True)
    (tweets_dir / "tweet_1.mp4").write_text("<html>not a video</html>", encoding="utf-8")
    monkeypatch.setattr(download, "run_x_media_download_lifecycle", lambda **_kwargs: type(
        "Lifecycle", (), {"command_result": None, "failure_reason": "x_media_download_failed", "details": {}, "attempt_dir": None}
    )())
    monkeypatch.setattr(download, "run_x_yt_dlp_fallback", lambda *_args, **_kwargs: type(
        "Fallback", (), {"status": "failed", "files": [], "provenance": {}, "evidence": {}}
    )())

    blocks = download.download_x_card_media(
        "card_bad_video",
        "https://x.com/alice/status/1",
        tmp_path,
    )

    assert blocks == []
