"""Offline acceptance of full-read evidence and the durable repair path."""
from __future__ import annotations


import pytest

from backend.application.enrichment.external_docs import enrich_external_docs
from backend.application.tasks.handlers.acquisition import handle_content_enrich
from backend.application.tasks.handlers.card_finalize import handle_card_finalize
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.transitions import enqueue_downstream
from backend.infrastructure.storage.tasks.finalization import TaskFinalizationQueue
from backend.infrastructure.storage.base import utc_now
from backend.domain.models.raw_item import RawItem
from backend.domain.tasks import Task, TaskStatus, TaskType
from backend.infrastructure.connectors.opencli import OpenCLIConnector
from backend.infrastructure.external_docs.feishu_browser import FeishuDocumentResult
from backend.infrastructure.storage.repositories import CardRepository, CardContentBlockRepository, RawItemRepository
from backend.read_models.card_preview import read_card_preview_detail

URL = "https://tenant.feishu.cn/docx/ABC"


def seed(base_dir, card_id="a", *, status="fetched", tail="帮助中心", complete=False):
    record = {"content_source": "feishu", "content_source_status": status,
              "content_source_url": URL, "title": "原帖及飞书链接 " + URL}
    if complete:
        record["content_source_completeness"] = "complete"
    content = "旧飞书首屏" * 30 + tail
    item = RawItem(raw_item_id=card_id, connector_id="zsxq-test", source_platform="zsxq",
                   source_type="article", source_url="https://wx.zsxq.com/topic/" + card_id,
                   captured_at="2026-10-12T00:00:00Z", raw_text=content,
                   raw_metadata={**record, "opencli_record": record})
    RawItemRepository(base_dir).upsert_raw_items([item])
    CardRepository(base_dir).upsert_card({"card_id": card_id, "source_platform": "zsxq",
        "source_type": "article", "source_url": item.source_url, "title": "帖子标题",
        "content": content, "summary": "", "captured_at": item.captured_at,
        "processed_at": item.captured_at, "raw_connector_id": item.connector_id,
        "raw_item_id": card_id, "raw_payload": {"raw_payload": item.raw_metadata}})
    return item


def finalize(base_dir, card_id="a"):
    return handle_card_finalize(Task(task_id="final-" + card_id, task_type=TaskType.CARD_FINALIZE,
                                    target_kind="card", target_id=card_id), base_dir)


@pytest.mark.parametrize("status", ["partial", "login_required", "empty"])
def test_source_status_alone_prevents_complete(tmp_path, status):
    seed(tmp_path, status=status)
    result = finalize(tmp_path)
    assert result["artifacts"]["completeness_status"] == "partial"
    assert "external_doc_" + status in CardRepository(tmp_path).get_card("a")["missing_reasons"]


@pytest.mark.parametrize("status", ["login_required", "empty"])
def test_title_is_not_a_failed_feishu_body(status):
    connector = OpenCLIConnector(connector_id="zsxq", command=["zsxq", "topics"],
                                 source_platform="zsxq", source_type="article", field_mapping={"text": "full_text"})
    record = {"title": "只是标题", "content_source": "feishu", "content_source_status": status}
    assert connector._to_raw_item(record).raw_text == ""
    record["full_text"] = "保留下来的原帖"
    assert connector._to_raw_item(record).raw_text == "保留下来的原帖"


@pytest.mark.parametrize("result_status,complete,reason", [
    ("fetched", False, "external_doc_partial"), ("fetched", True, None),
    ("blocked", False, "external_doc_login_required"), ("blocked", False, "external_doc_empty"),
])
def test_shorter_or_failed_read_retains_card_and_existing_block(tmp_path, monkeypatch, result_status, complete, reason):
    seed(tmp_path)
    before = CardRepository(tmp_path).get_card("a")["content"]
    repo = CardContentBlockRepository(tmp_path)
    repo.upsert_block({"block_id": "block_a_external_doc_feishu", "card_id": "a", "layer": "external",
                      "block_kind": "external_doc", "status": "body_fetched", "body": before,
                      "raw_payload": {"provider": "feishu", "fetch_state": "fetched"}})
    monkeypatch.setattr("backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(status=result_status, source_url=url, body="少量文字",
                                        complete=complete, reason=reason))
    for _ in range(2):
        enrich_external_docs("a", tmp_path)
        assert CardRepository(tmp_path).get_card("a")["content"] == before
        assert repo.list_for_card("a")[0]["body"] == before
        assert finalize(tmp_path)["artifacts"]["completeness_status"] == "partial"


def test_partial_external_block_is_not_complete_even_with_body_fetched(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.setattr("backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(status="fetched", source_url=url, body="未读全的正文" * 100))
    enrich_external_docs("a", tmp_path)
    assert finalize(tmp_path)["artifacts"]["completeness_status"] == "partial"
    assert "external_doc_partial" in CardRepository(tmp_path).get_card("a")["missing_reasons"]


def test_backfill_keeps_evaluations_and_entry_time(tmp_path, monkeypatch):
    seed(tmp_path)
    finalize(tmp_path)
    repo = CardRepository(tmp_path)
    with repo.connect() as conn:
        first_seen = conn.execute("select first_seen_at from raw_items").fetchone()[0]
        conn.execute("insert into interest_profiles values (1, '画像', '{}', 'hash', 'test', '2026-10-12T00:00:00Z')")
        conn.execute("""insert into card_evaluations (
            evaluation_id, card_id, card_version, profile_version, prompt_version, model, provider_id,
            quality, relevance, content_type, suggested_action, paused_landmark, summary,
            relation_note, passed_quality, created_at)
            values ('evaluation-1', 'a', 1, 1, 'v1', 'external-agent', 'agent',
                    80, 90, 'method', 'read', 0, '原评估', '相关', 1, '2026-10-12T00:00:00Z')""")
    monkeypatch.setattr("backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(status="fetched", source_url=url, body="完整正文" * 200, complete=True))
    enrich_external_docs("a", tmp_path)
    finalize(tmp_path)
    with repo.connect_existing() as conn:
        evaluation = dict(conn.execute("select evaluation_id, card_version, summary from card_evaluations").fetchone())
        assert evaluation == {"evaluation_id": "evaluation-1", "card_version": 1, "summary": "原评估"}
        assert conn.execute("select first_seen_at from raw_items").fetchone()[0] == first_seen
        revisions = conn.execute("select revision, raw_text from raw_item_revisions order by revision").fetchall()
        assert len(revisions) == 2 and "帮助中心" in revisions[0]["raw_text"]


def test_shorter_read_replaces_stored_body_only_when_complete_and_close():
    from backend.domain.feishu_content import keeps_previous_body

    assert keeps_previous_body(900, 1000, complete=False) is True
    assert keeps_previous_body(900, 1000, complete=True) is False
    assert keeps_previous_body(700, 1000, complete=True) is True
    assert keeps_previous_body(1200, 1000, complete=False) is False


def test_full_read_publishes_body_revisions_and_finalizes(tmp_path, monkeypatch):
    item = seed(tmp_path)
    finalize(tmp_path)
    old_version = CardRepository(tmp_path).get_card("a")["version"]
    monkeypatch.setattr("backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(status="fetched", source_url=url, body="完整正文" * 200,
                                        complete=True, blocks=328, reached_end=True))
    repo = TaskRepository.for_base_dir(tmp_path)
    task = Task(task_id="full-read-a", task_type=TaskType.CONTENT_ENRICH,
                target_kind="card", target_id="a", input_version="full-read-v1",
                artifacts_summary={"card_id": "a", "feishu_backfill": True})
    repo.enqueue(task)
    result = handle_content_enrich(task, tmp_path)
    repo.update_status(task.task_id, TaskStatus.SUCCESS)
    with CardRepository(tmp_path).connect() as conn:
        queue = TaskFinalizationQueue(conn, now=utc_now())
        children = enqueue_downstream(queue, task, status=TaskStatus.SUCCESS,
                                      artifacts=result["artifacts"], base_dir=tmp_path)
    assert len(children) == 1 and repo.get(children[0]).task_type == TaskType.CARD_FINALIZE
    handle_card_finalize(repo.get(children[0]), tmp_path)
    card = CardRepository(tmp_path).get_card("a")
    assert card["content"] == "原帖及飞书链接 " + URL + "\n\n" + "完整正文" * 200
    assert card["card_id"] == "a" and card["raw_item_id"] == item.raw_item_id
    assert card["version"] == old_version + 1 and card["completeness_status"] == "complete"
    preview = read_card_preview_detail(tmp_path, "a")
    assert "\n\n".join(block["body"] for block in preview["body_blocks"] if block["layer"] == "original") == card["content"]
    blocks = CardContentBlockRepository(tmp_path).list_for_card("a")
    assert len(blocks) == 1 and blocks[0]["body"] == "完整正文" * 200
    assert blocks[0]["raw_payload"]["completeness"] == "complete"
    raw = RawItemRepository(tmp_path).get_model_for_connector("zsxq-test", "a")
    assert raw.raw_text == card["content"]
    with CardRepository(tmp_path).connect_existing() as conn:
        assert conn.execute("select count(*) from raw_item_revisions").fetchone()[0] == 2
    enrich_external_docs("a", tmp_path)
    assert len(CardContentBlockRepository(tmp_path).list_for_card("a")) == 1
    assert finalize(tmp_path)["artifacts"]["changed"] is False
