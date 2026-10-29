"""Article enrichment acceptance: discovery, publication, routing and repair."""
from __future__ import annotations

import pytest

from backend.application.enrichment.external_doc_inputs import external_doc_inputs
from backend.application.enrichment.task_blocks import enrich_card_blocks_for_task
from backend.application.tasks.handlers.acquisition import handle_content_enrich
from backend.application.tasks.handlers.card_finalize import handle_card_finalize
from backend.application.tasks.lanes import ExecutionLane, execution_lane_and_site_for
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.transitions import enqueue_downstream
from backend.application.usecases.zsxq_article_backfill import backfill_zsxq_articles
from backend.domain.external_doc_policy import content_enrich_needs_browser
from backend.domain.models.raw_item import RawItem
from backend.domain.tasks import Task, TaskStatus, TaskType
from backend.domain.zsxq_article import article_gap, article_url
from backend.infrastructure.external_docs.feishu_browser import FeishuDocumentResult
from backend.infrastructure.external_docs.zsxq_article_browser import ZsxqArticleResult
from backend.infrastructure.storage.base import utc_now
from backend.infrastructure.storage.repositories import CardRepository, RawItemRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.tasks.finalization import TaskFinalizationQueue

URL = 'https://articles.zsxq.com/id_abc.html'
FEISHU = 'https://my.feishu.cn/docx/body'
IMAGE = 'https://article-images.zsxq.com/image'


def seed(base_dir, card_id='a', *, metadata=None, platform='zsxq'):
    item = RawItem(raw_item_id=card_id, connector_id='zsxq-test', source_platform=platform,
                   source_type='article', source_url='https://wx.zsxq.com/topic/' + card_id,
                   captured_at='2026-10-22T00:00:00Z', raw_text='标题\n截断预览 ...',
                   raw_metadata=metadata if metadata is not None else {'links': [URL]})
    RawItemRepository(base_dir).upsert_raw_items([item])
    CardRepository(base_dir).upsert_card({
        'card_id': card_id, 'source_platform': platform, 'source_type': 'article',
        'source_url': item.source_url, 'title': '截断预览 ...', 'content': item.raw_text,
        'captured_at': item.captured_at, 'processed_at': item.captured_at,
        'raw_connector_id': item.connector_id, 'raw_item_id': card_id, 'raw_payload': {},
    })
    return item


@pytest.mark.parametrize('payload', [
    {'article_url': 'https://articles.zsxq.com/inline_form/id_abc.html?foo=1'},
    {'raw_payload': {'links': [URL]}},
    {'raw_metadata': {'opencli_record': {'links': [URL]}}},
])
def test_article_discovery(payload):
    assert article_url(payload) == URL


@pytest.mark.parametrize('status', ['pending', 'login_required', 'empty', 'failed', 'fetched'])
def test_article_gap_and_finalize(tmp_path, status):
    seed(tmp_path, metadata={'links': [URL], 'zsxq_article_read': {'status': status}})
    card = CardRepository(tmp_path).get_card('a')
    inputs = external_doc_inputs(card, tmp_path)
    assert article_gap(inputs[0]) == (None if status == 'fetched' else 'zsxq_article_' + status)
    result = handle_card_finalize(Task('final', TaskType.CARD_FINALIZE, 'card', 'a'), tmp_path)
    assert result['artifacts']['completeness_status'] == ('complete' if status == 'fetched' else 'partial')
    assert ('zsxq_article_' + status in CardRepository(tmp_path).get_card('a')['missing_reasons']) == (status != 'fetched')


def test_article_then_feishu_preserves_article_and_repeats_without_preview_or_duplication(tmp_path, monkeypatch):
    seed(tmp_path, metadata={'links': [URL], 'content_source': 'feishu', 'content_source_status': 'partial',
                             'original_content': '截断预览 ...'})
    calls = []
    def article(url):
        calls.append(('article', url))
        return ZsxqArticleResult('fetched', url, title='文章标题', body='完整文章正文。' * 20,
                                 complete=True, links=[FEISHU], images=[IMAGE])
    def feishu(url):
        calls.append(('feishu', url))
        return FeishuDocumentResult('fetched', url, body='飞书全文。' * 30, complete=True)
    monkeypatch.setattr('backend.application.enrichment.zsxq_article.fetch_zsxq_article', article)
    monkeypatch.setattr('backend.application.enrichment.external_docs.fetch_feishu_document', feishu)
    expected = '文章标题\n\n' + '完整文章正文。' * 20 + '\n\n' + '飞书全文。' * 30
    for _ in range(2):
        enrich_card_blocks_for_task('a', tmp_path)
        card = CardRepository(tmp_path).get_card('a')
        assert card['content'] == expected
        raw = RawItemRepository(tmp_path).get_model_for_connector('zsxq-test', 'a')
        assert raw.raw_text == expected
        assert IMAGE in raw.raw_metadata['images']
        assert FEISHU in raw.raw_metadata['links']
    assert calls == [('article', URL), ('feishu', FEISHU)]


def test_article_browser_routing_and_circuit_deferral(tmp_path, monkeypatch):
    seed(tmp_path)
    card = CardRepository(tmp_path).get_card('a')
    task = Task('enrich', TaskType.CONTENT_ENRICH, 'card', 'a')
    assert content_enrich_needs_browser(*external_doc_inputs(card, tmp_path)) is True
    assert execution_lane_and_site_for(task, tmp_path) == (ExecutionLane.BROWSER_ACQUISITION, 'zsxq')
    RuntimeCircuitRepository(tmp_path).open_manual('platform', 'zsxq', reason_code='platform_not_logged_in',
                                                 occurred_at=utc_now(), user_action='login')
    def unexpected(url):
        pytest.fail('open circuit must not read an article page')
    monkeypatch.setattr('backend.application.enrichment.zsxq_article.fetch_zsxq_article', unexpected)
    assert handle_content_enrich(task, tmp_path)['status'] == 'pending'
    assert CardRepository(tmp_path).get_card('a')['content'].endswith(' ...')


def test_article_backfill_candidates_idempotence_and_media_chain(tmp_path):
    seed(tmp_path, 'a')
    seed(tmp_path, 'b', metadata={'article_url': URL, 'zsxq_article_read': {'status': 'fetched'}})
    seed(tmp_path, 'c', metadata={})
    seed(tmp_path, 'd', platform='scys')
    preview = backfill_zsxq_articles(tmp_path, limit=1)
    assert preview['dry_run'] is True and preview['count'] == 1 and not preview['enqueued']
    assert backfill_zsxq_articles(tmp_path, limit=200, after_card_id='a')['count'] == 0
    applied = backfill_zsxq_articles(tmp_path, limit=200, apply=True)
    assert len(applied['enqueued']) == 1
    assert backfill_zsxq_articles(tmp_path, limit=200, apply=True)['already_queued'] == ['a']
    repo = TaskRepository.for_base_dir(tmp_path)
    task = repo.get(applied['enqueued'][0])
    assert task.artifacts_summary['zsxq_article_backfill'] is True
    repo.update_status(task.task_id, TaskStatus.SUCCESS)
    with CardRepository(tmp_path).connect() as conn:
        children = enqueue_downstream(TaskFinalizationQueue(conn, now=utc_now()), task,
                                      status=TaskStatus.SUCCESS, artifacts={}, base_dir=tmp_path)
    assert len(children) == 1 and repo.get(children[0]).task_type == TaskType.MEDIA_DOWNLOAD


@pytest.mark.parametrize('status', ['login_required', 'empty', 'failed'])
def test_failed_article_read_keeps_body_and_persists_gap(tmp_path, monkeypatch, status):
    seed(tmp_path)
    monkeypatch.setattr('backend.application.enrichment.zsxq_article.fetch_zsxq_article',
                        lambda url: ZsxqArticleResult(status, url))
    task = Task('enrich', TaskType.CONTENT_ENRICH, 'card', 'a')
    if status == 'failed':
        with pytest.raises(RuntimeError, match='zsxq_article_browser_failed'):
            handle_content_enrich(task, tmp_path)
    else:
        assert handle_content_enrich(task, tmp_path)['status'] == 'success'
    card = CardRepository(tmp_path).get_card('a')
    assert card['content'] == '标题\n截断预览 ...'
    assert article_gap(card) == 'zsxq_article_' + status


def test_article_alone_and_shorter_result_publication(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.setattr('backend.application.enrichment.zsxq_article.fetch_zsxq_article',
        lambda url: ZsxqArticleResult('fetched', url, title='标题', body='完整正文' * 30, complete=True))
    enrich_card_blocks_for_task('a', tmp_path)
    assert CardRepository(tmp_path).get_card('a')['content'] == '标题\n\n' + '完整正文' * 30
    seed(tmp_path, 'long')
    from backend.infrastructure.storage.zsxq_article import ZsxqArticleRepository
    repo = ZsxqArticleRepository(tmp_path)
    repo.publish_read('long', evidence={'article_url': URL, 'status': 'fetched'},
                      post='已有正文' * 100, links=[], images=[])
    repo.publish_read('long', evidence={'article_url': URL, 'status': 'fetched'},
                      post='短文', links=[], images=[])
    card = CardRepository(tmp_path).get_card('long')
    assert card['content'] == '已有正文' * 100
    assert article_gap(card) == 'zsxq_article_partial'


def test_article_and_feishu_use_sequential_site_leases(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import json
    from types import SimpleNamespace

    seed(tmp_path)
    active = []
    sites = []
    @contextmanager
    def lease(site, **kwargs):
        assert active == []
        active.append(site)
        sites.append(site)
        yield
        active.pop()
    def command(args, **kwargs):
        site = args[2]
        assert active == [site]
        assert args[:2] == ['--profile', 'infoos-fetch']
        if site == 'zsxq':
            data = {'url': URL, 'status': 'fetched', 'title': '标题', 'body': '文章正文' * 30,
                    'complete': True, 'links': [FEISHU], 'images': [IMAGE]}
        else:
            data = {'url': FEISHU, 'body': '飞书全文' * 30, 'complete': True,
                    'completeness': 'complete', 'reached_end': True}
        return SimpleNamespace(returncode=0, stdout=json.dumps([data]))
    for module in ['zsxq_article_browser', 'feishu_browser']:
        prefix = 'backend.infrastructure.external_docs.' + module
        monkeypatch.setattr(prefix + '.opencli_tab_lease', lease)
        monkeypatch.setattr(prefix + '.run_opencli', command)
        monkeypatch.setattr(prefix + '.current_opencli_context_id', lambda: 'infoos-fetch')
    enrich_card_blocks_for_task('a', tmp_path)
    assert sites == ['zsxq', 'feishu'] and not active
    from backend.application.enrichment.media.candidates import collect_media_candidates
    assert IMAGE in [candidate.url for candidate in collect_media_candidates(CardRepository(tmp_path).get_card('a'), tmp_path)]
