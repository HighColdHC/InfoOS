from __future__ import annotations

from pathlib import Path

from tests.infoos._factories import card_count
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository, RawItemRepository, SourceRepository
from backend.application.enrichment.media.artifacts import collect_generic_media_urls
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.registry import handler_for
from backend.application.tasks.worker import run_due_tasks
from backend.domain.models.raw_item import RawAuthor, RawItem


def _raw_item(raw_item_id: str = "raw_1") -> RawItem:
    return RawItem(
        raw_item_id=raw_item_id,
        connector_id="rss_source",
        source_platform="rss",
        source_type="article",
        source_url=f"https://example.com/{raw_item_id}",
        captured_at="2026-07-10T00:00:00Z",
        raw_text="A detailed intelligence item with evidence and a concrete next step.",
        raw_author=RawAuthor(name="Example"),
        raw_metadata={"source_id": "rss_source", "title": "Card build target"},
    )


def _upsert_rss_source(base_dir: Path) -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": "rss_source",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "RSS source",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_source",
        }
    )


def test_card_build_task_targets_raw_item(tmp_path: Path):
    _upsert_rss_source(tmp_path)
    RawItemRepository(tmp_path).upsert_raw_items([_raw_item()])

    result = handler_for(TaskType.CARD_BUILD)(
        Task(
            task_id="card_build_raw_1",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id="raw_1",
        ),
        tmp_path,
    )

    assert result["status"] == TaskStatus.SUCCESS.value
    assert result["artifacts"]["raw_item_id"] == "raw_1"
    assert result["artifacts"]["created_or_updated"] == "created"
    assert CardRepository(tmp_path).get_card(result["artifacts"]["card_id"]) is not None


def test_card_build_is_idempotent_for_existing_card(tmp_path: Path):
    _upsert_rss_source(tmp_path)
    RawItemRepository(tmp_path).upsert_raw_items([_raw_item()])
    task = Task(
        task_id="card_build_raw_1",
        task_type=TaskType.CARD_BUILD,
        target_kind="raw_item",
        target_id="raw_1",
    )

    first = handler_for(TaskType.CARD_BUILD)(task, tmp_path)
    second = handler_for(TaskType.CARD_BUILD)(task, tmp_path)

    assert first["artifacts"]["card_id"] == second["artifacts"]["card_id"]
    assert first["artifacts"]["created_or_updated"] == "created"
    assert second["artifacts"]["created_or_updated"] == "updated"
    assert card_count(tmp_path) == 1


def test_exact_card_rebuild_preserves_proven_terminal_state(tmp_path: Path):
    _upsert_rss_source(tmp_path)
    RawItemRepository(tmp_path).upsert_raw_items([_raw_item()])
    task = Task(
        task_id="card_build_raw_1",
        task_type=TaskType.CARD_BUILD,
        target_kind="raw_item",
        target_id="raw_1",
    )
    first = handler_for(TaskType.CARD_BUILD)(task, tmp_path)
    card_id = str(first["artifacts"]["card_id"])
    CardRepository(tmp_path).finalize_card(
        card_id,
        content_hash=f"sha256:{'a' * 64}",
        completeness_status="complete",
        missing_reasons=[],
    )

    handler_for(TaskType.CARD_BUILD)(task, tmp_path)
    rebuilt = CardRepository(tmp_path).get_card(card_id)

    assert rebuilt is not None
    assert rebuilt["status"] == "active"
    assert rebuilt["completeness_status"] == "complete"
    assert rebuilt["content_hash"] == f"sha256:{'a' * 64}"


def test_card_build_blocks_when_raw_item_is_missing(tmp_path: Path):
    result = handler_for(TaskType.CARD_BUILD)(
        Task(
            task_id="card_build_missing",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id="missing",
        ),
        tmp_path,
    )

    assert result["status"] == TaskStatus.BLOCKED.value
    assert result["error"] == "raw_item_missing"


def test_fetch_handler_does_not_call_run_once(tmp_path: Path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("run_once must not run inside FETCH")

    monkeypatch.setattr("backend.application.workflows.run_once.run_once", fail_if_called)
    monkeypatch.setattr(
        "backend.application.workflows.acquisition.fetch_source",
        lambda base_dir, *, source_id, task: {
            "source_id": source_id,
            "raw_item_ids": ["raw_target"],
            "card_build_task_ids": ["card_build_raw_target"],
        },
    )
    task = Task(
        task_id="fetch_source_a",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source_a",
    )

    result = handler_for(TaskType.FETCH)(task, tmp_path)

    assert result["status"] == "success"
    assert result["artifacts"]["raw_item_ids"] == ["raw_target"]
    assert result["artifacts"]["card_build_task_ids"] == ["card_build_raw_target"]


def test_content_enrich_task_adds_reddit_content_blocks(tmp_path: Path):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "reddit_local",
            "platform": "reddit",
            "source_type": "subreddit",
            "identifier": "LocalLLaMA",
            "display_name": "LocalLLaMA",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "reddit_local",
        }
    )
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_reddit_1",
            "raw_connector_id": "reddit_local",
            "raw_item_id": "abc",
            "source_id": "reddit_local",
            "connector_id": "reddit_local",
            "source_platform": "reddit",
            "source_type": "social_post",
            "source_url": "https://www.reddit.com/r/LocalLLaMA/comments/abc/test/",
            "source_author": "llama_user",
            "title": "Local model field report",
            "content": "Local model field report",
            "captured_at": "2026-06-30T00:00:00Z",
            "processed_at": "2026-06-30T00:00:00Z",
            "raw_payload": {},
        }
    )
    RawItemRepository(tmp_path).upsert_raw_items(
        [
            RawItem(
                raw_item_id="abc",
                connector_id="reddit_local",
                source_platform="reddit",
                source_type="social_post",
                source_url="https://www.reddit.com/r/LocalLLaMA/comments/abc/test/",
                captured_at="2026-06-30T00:00:00Z",
                raw_text="Local model field report",
                raw_author=RawAuthor(name="llama_user", handle="llama_user"),
                raw_metadata={
                    "title": "Local model field report",
                    "subreddit": "LocalLLaMA",
                    "selftext": "A practical note from a local model deployment.",
                    "score": 42,
                    "comments": 7,
                    "url_overridden_by_dest": "https://example.com/local-model-report",
                },
            )
        ]
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="content_enrich_card_reddit_1",
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id="card_reddit_1",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["succeeded"] == 1
    task = repo.get("content_enrich_card_reddit_1")
    assert task.status == TaskStatus.SUCCESS
    assert task.artifacts_summary["blocks_added"] == 3
    blocks = CardContentBlockRepository(tmp_path).list_for_card("card_reddit_1")
    assert [block["block_kind"] for block in blocks] == [
        "reddit_post",
        "reddit_metrics",
        "reddit_outbound_link",
    ]
    assert "A practical note" in blocks[0]["body"]
    assert blocks[1]["body"] == "r/LocalLLaMA · u/llama_user · score: 42 · comments: 7"


def test_content_enrich_task_adds_generic_linked_article_block(tmp_path: Path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "RSS AI",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "rss_ai",
        }
    )
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_rss_1",
            "raw_connector_id": "rss_ai",
            "raw_item_id": "rss-1",
            "source_id": "rss_ai",
            "connector_id": "rss_ai",
            "source_platform": "rss",
            "source_type": "article",
            "source_url": "https://example.com/thin",
            "source_author": "Example",
            "title": "Thin article",
            "content": "Thin article",
            "captured_at": "2026-06-30T00:00:00Z",
            "processed_at": "2026-06-30T00:00:00Z",
            "raw_payload": {},
        }
    )
    RawItemRepository(tmp_path).upsert_raw_items(
        [
            RawItem(
                raw_item_id="rss-1",
                connector_id="rss_ai",
                source_platform="rss",
                source_type="article",
                source_url="https://example.com/thin",
                captured_at="2026-06-30T00:00:00Z",
                raw_text="Thin article",
                raw_metadata={"title": "Thin article"},
            )
        ]
    )

    def fake_enrich(raw_item):
        return raw_item.model_copy(
            update={
                "raw_text": "Full article body with enough substance for downstream AI summary.",
                "raw_metadata": {**(raw_item.raw_metadata or {}), "enrichment_applied": True},
            }
        )

    monkeypatch.setattr("backend.application.enrichment.web_content.enrich_raw_item_content", fake_enrich)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="content_enrich_card_rss_1",
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id="card_rss_1",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["succeeded"] == 1
    task = repo.get("content_enrich_card_rss_1")
    assert task.status == TaskStatus.SUCCESS
    assert task.artifacts_summary["blocks_added"] == 1
    blocks = CardContentBlockRepository(tmp_path).list_for_card("card_rss_1")
    assert [block["block_kind"] for block in blocks] == ["linked_article"]
    assert blocks[0]["status"] == "body_fetched"
    assert "Full article body" in blocks[0]["body"]


def test_generic_content_enrichment_skips_xiaohongshu_platform_page(tmp_path: Path, monkeypatch):
    from backend.application.enrichment.generic_blocks import enrich_generic_card_blocks

    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "xhs_source",
            "platform": "xiaohongshu",
            "source_type": "keyword",
            "identifier": "人工智能",
            "display_name": "小红书 AI",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "xhs_source",
        }
    )
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_xhs_platform_page",
            "source_id": "xhs_source",
            "connector_id": "xhs_source",
            "source_platform": "xiaohongshu",
            "source_type": "social_post",
            "source_url": "https://www.xiaohongshu.com/explore/note-1",
            "title": "小红书帖子",
            "content": "帖子原始正文",
            "captured_at": "2026-07-19T00:00:00Z",
            "processed_at": "2026-07-19T00:00:00Z",
            "raw_payload": {},
        }
    )
    RawItemRepository(tmp_path).upsert_raw_items(
        [
            RawItem(
                raw_item_id="xhs-note-1",
                connector_id="xhs_source",
                source_platform="xiaohongshu",
                source_type="social_post",
                source_url="https://www.xiaohongshu.com/explore/note-1",
                captured_at="2026-07-19T00:00:00Z",
                raw_text="帖子原始正文",
            )
        ]
    )

    def unexpected_page_fetch(_raw_item):
        raise AssertionError("platform page must not enter generic article enrichment")

    monkeypatch.setattr(
        "backend.application.enrichment.web_content.enrich_raw_item_content",
        unexpected_page_fetch,
    )

    assert enrich_generic_card_blocks("card_xhs_platform_page", tmp_path) == []
    assert CardContentBlockRepository(tmp_path).list_for_card("card_xhs_platform_page") == []


def test_content_enrich_persists_page_media_when_article_body_matches_feed(tmp_path: Path, monkeypatch):
    _upsert_rss_source(tmp_path)
    body = "The complete feed body is already identical to the linked article body."
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_rss_media_only",
            "raw_connector_id": "rss_source",
            "raw_item_id": "rss-media-only",
            "source_id": "rss_source",
            "connector_id": "rss_source",
            "source_platform": "rss",
            "source_type": "article",
            "source_url": "https://example.com/media-only",
            "title": "Media-only enrichment",
            "content": body,
            "captured_at": "2026-07-17T00:00:00Z",
            "processed_at": "2026-07-17T00:00:00Z",
            "raw_payload": {},
        }
    )
    RawItemRepository(tmp_path).upsert_raw_items(
        [
            RawItem(
                raw_item_id="rss-media-only",
                connector_id="rss_source",
                source_platform="rss",
                source_type="article",
                source_url="https://example.com/media-only",
                captured_at="2026-07-17T00:00:00Z",
                raw_text=body,
                raw_metadata={"title": "Media-only enrichment"},
            )
        ]
    )

    def fake_enrich(raw_item):
        return raw_item.model_copy(
            update={
                "raw_metadata": {
                    **(raw_item.raw_metadata or {}),
                    "enrichment_applied": True,
                    "enrichment_source_url": raw_item.source_url,
                    "images": ["https://cdn.example.com/page-only.jpg"],
                    "videos": ["https://cdn.example.com/page-only.mp4"],
                }
            }
        )

    monkeypatch.setattr("backend.application.enrichment.web_content.enrich_raw_item_content", fake_enrich)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="content_enrich_card_rss_media_only",
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id="card_rss_media_only",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["succeeded"] == 1
    blocks = CardContentBlockRepository(tmp_path).list_for_card("card_rss_media_only")
    assert len(blocks) == 1
    assert blocks[0]["block_kind"] == "linked_article"
    assert blocks[0]["body"] == ""
    assert collect_generic_media_urls(
        CardRepository(tmp_path).get_card("card_rss_media_only"), tmp_path
    ) == [
        "https://cdn.example.com/page-only.jpg",
        "https://cdn.example.com/page-only.mp4",
    ]


def test_content_enrich_task_reports_platform_comment_gaps(tmp_path: Path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "xhs_source",
            "platform": "xiaohongshu",
            "source_type": "keyword",
            "identifier": "AI",
            "display_name": "小红书 AI",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "xhs_source",
        }
    )
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_xhs_gap",
            "source_id": "xhs_source",
            "connector_id": "xhs_source",
            "source_platform": "xiaohongshu",
            "source_type": "social_post",
            "source_url": "https://www.xiaohongshu.com/search_result/1",
            "source_author": "author",
            "title": "小红书笔记",
            "content": "基础正文",
            "captured_at": "2026-07-02T00:00:00Z",
            "processed_at": "2026-07-02T00:00:00Z",
            "raw_payload": {},
        }
    )
    monkeypatch.setattr(
        "backend.application.enrichment.platform_comments._fetch_xiaohongshu_comments",
        lambda note_url: [],
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="content_enrich_card_xhs_gap",
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id="card_xhs_gap",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["succeeded"] == 1
    task = repo.get("content_enrich_card_xhs_gap")
    assert task.status == TaskStatus.SUCCESS
    assert task.artifacts_summary["blocked_blocks"] == 1
    assert task.artifacts_summary["gap_reasons"] == ["platform_comments_unavailable"]


def test_content_enrich_task_reports_platform_comment_exceptions(tmp_path: Path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "xhs_source",
            "platform": "xiaohongshu",
            "source_type": "keyword",
            "identifier": "AI",
            "display_name": "小红书 AI",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "xhs_source",
        }
    )
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_xhs_exception",
            "source_id": "xhs_source",
            "connector_id": "xhs_source",
            "source_platform": "xiaohongshu",
            "source_type": "social_post",
            "source_url": "https://www.xiaohongshu.com/search_result/exception",
            "source_author": "author",
            "title": "小红书笔记",
            "content": "基础正文",
            "captured_at": "2026-07-02T00:00:00Z",
            "processed_at": "2026-07-02T00:00:00Z",
            "raw_payload": {},
        }
    )

    def raise_comments(note_url):
        raise OSError("boom")

    monkeypatch.setattr(
        "backend.application.enrichment.platform_comments._fetch_xiaohongshu_comments",
        raise_comments,
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="content_enrich_card_xhs_exception",
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id="card_xhs_exception",
        )
    )

    result = run_due_tasks(tmp_path, max_workers=1)

    assert result["succeeded"] == 1
    task = repo.get("content_enrich_card_xhs_exception")
    assert task.status == TaskStatus.SUCCESS
    assert task.artifacts_summary["blocked_blocks"] == 1
    assert task.artifacts_summary["gap_reasons"] == ["platform_comments_exception"]
