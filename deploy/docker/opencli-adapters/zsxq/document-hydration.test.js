import assert from 'node:assert/strict';
import test from 'node:test';
import { createHydrationBudget, mergeFeishuDocument } from './document-hydration.js';

const url = 'https://tenant.feishu.cn/docx/ABC';
const original = { content: '星球原文', images: ['https://cdn.example/image'], videos: [] };
for (const [status, document] of [
  ['fetched', { body: '飞书全文', complete: true, completeness: 'complete', blocks: 30, reached_end: true }],
  ['partial', { body: '飞书首屏', complete: false, completeness: 'partial', blocks: 3, reached_end: false }],
  ['login_required', { body: '登录页界面', login_required: true }],
  ['empty', { body: '' }],
]) {
  test(`hydration preserves ZSXQ original and exposes ${status} accurately`, () => {
    const row = mergeFeishuDocument(original, document, url);
    assert.equal(row.content_source_status, status);
    assert.equal(row.original_content, '星球原文');
    assert.equal(row.content_source_complete, status === 'fetched');
    assert.equal(row.content_source_completeness, status === 'fetched' ? 'complete' : 'partial');
    assert.equal(row.content, ['星球原文', status === 'login_required' ? '' : document.body].filter(Boolean).join('\n\n'));
    assert.equal(row.full_text, row.content);
  });
}

test('budget-exhausted documents stay partial without discarding original text', () => {
  const row = mergeFeishuDocument(original, {}, url, true);
  assert.equal(row.content_source_status, 'partial');
  assert.equal(row.content, '星球原文');
});

test('hydration deduplicates images, retains video and authenticated media facts', () => {
  const blob = { url: 'https://tenant.feishu.cn/image', base64: 'AA==', mime_type: 'image/png' };
  const row = mergeFeishuDocument(original, {
    body: '正文', images: [...original.images, 'https://cdn.example/second'],
    videos: ['https://cdn.example/video'], media_blobs: [blob],
  }, url);
  assert.equal(row.images.length, 2);
  assert.deepEqual(row.media_blobs, [blob]);
  assert.equal(row.videos.length, 1);
});

test('list budget aligns with 40 seconds per document and 540 second ceiling', () => {
  for (const [limit, expected] of [[1, 40000], [2, 80000], [20, 540000], [100, 540000]]) {
    const start = Date.now();
    const budget = createHydrationBudget(limit);
    assert.ok(budget.deadlineMs >= start + expected);
    assert.ok(budget.deadlineMs <= Date.now() + expected);
  }
});
