import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { collectDocument } from './document-script.js';

function simulatedDocument({ frames = [], body = '旧版文档正文', budgetMs = 40000, scrollable = true, mediaDomain = 'cdn.example' } = {}) {
  let now = 0;
  let top = 0;
  const container = {
    clientHeight: 100,
    get scrollHeight() { return scrollable ? Math.max(200, (Math.min(frames.length, Math.floor(top / 80) + 2)) * 80 + 100) : 100; },
    get scrollTop() { return top; },
    set scrollTop(value) { top = Math.min(value, this.scrollHeight - this.clientHeight); },
  };
  const current = () => frames[Math.min(frames.length - 1, Math.floor(top / 80))] || [];
  const document = {
    title: '标题', body: { innerText: body }, scrollingElement: container,
    querySelector: () => null,
    querySelectorAll: (selector) => {
      if (selector === '*') return scrollable ? [container] : [];
      if (selector === '[data-block-id]') return current().map(([id, text, parent = false]) => ({
        innerText: text, getAttribute: () => id, querySelector: () => parent ? {} : null,
      }));
      if (selector === 'img') return current().map(([id]) => ({ src: `https://${mediaDomain}/${id}`, currentSrc: '' }));
      return [];
    },
  };
  const context = {
    document, window: {}, location: { href: 'https://tenant.feishu.cn/docx/ABC', pathname: '/docx/ABC' },
    URL, AbortController, btoa, fetch: async (url) => ({
      ok: true, url, headers: { get: () => 'image/png' },
      arrayBuffer: async () => Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10]).buffer,
    }), getComputedStyle: () => ({ overflowY: 'auto' }),
    Date: { now: () => now },
    setTimeout: (callback, duration) => { if (duration <= 700) { now += duration; callback(); } return 1; },
    clearTimeout: () => {},
  };
  return vm.runInNewContext(`(${collectDocument.toString()})(${JSON.stringify({ budgetMs })})`, context);
}

test('executable DOM accumulates virtual blocks in order, deduplicates media and reaches a growing end', async () => {
  const row = await simulatedDocument({ frames: [
    [['parent', '父级重复文字', true], ['a', '第一段'], ['b', '第二段']],
    [['b', '第二段'], ['c', '第三段']],
    [['c', '第三段'], ['d', '末段']],
  ] });
  assert.equal(row.body, '第一段\n第二段\n第三段\n末段');
  assert.equal(row.blocks, 4);
  assert.equal(row.reached_end, true);
  assert.equal(row.complete, true);
  assert.equal(row.completeness, 'complete');
  assert.equal(row.images.length, 5);
  assert.doesNotMatch(row.body, /父级|旧版/);
});

test('a block first seen as an empty placeholder keeps its later, longer text', async () => {
  const row = await simulatedDocument({ frames: [
    [['a', ''], ['b', '第二']],
    [['a', '第一段全文'], ['b', '第二段全文'], ['c', '第三段']],
    [['c', '第三段']],
  ] });
  assert.equal(row.body, '第一段全文\n第二段全文\n第三段');
  assert.equal(row.complete, true);
});

test('executable DOM returns accumulated content as partial when time budget expires', async () => {
  const row = await simulatedDocument({ budgetMs: 1100, frames: [
    [['a', '开头']], [['b', '中段']], [['c', '末尾']],
  ] });
  assert.equal(row.body, '开头\n中段');
  assert.equal(row.complete, false);
  assert.equal(row.reached_end, false);
});

test('executable DOM declares a non-scrollable block document complete', async () => {
  const row = await simulatedDocument({ scrollable: false, frames: [[['a', '完整短文']]] });
  assert.equal(row.complete, true);
  assert.equal(row.blocks, 1);
});

test('executable DOM falls back to visible text and marks long scrolling pages partial', async () => {
  for (const scrollable of [false, true]) {
    const row = await simulatedDocument({ scrollable, body: '旧版内容 帮助中心' });
    assert.equal(row.body, '旧版内容 帮助中心');
    assert.equal(row.blocks, 0);
    assert.equal(row.complete, !scrollable);
  }
});

test('executable DOM stops at the character cap and reports partial', async () => {
  const row = await simulatedDocument({ frames: [[['a', '字'.repeat(120001)]]] });
  assert.equal(row.body.length, 120000);
  assert.equal(row.complete, false);
});

test('executable DOM accumulates authenticated media blobs across unmounted screens', async () => {
  const row = await simulatedDocument({ mediaDomain: 'tenant.feishu.cn', frames: [
    [['a', '前段']], [['a', '前段'], ['b', '后段']], [['c', '末段']],
  ] });
  assert.equal(row.media_blobs.length, 3);
  assert.equal(new Set(row.media_blobs.map((blob) => blob.url)).size, 3);
  assert.ok(row.media_blobs.every((blob) => blob.mime_type === 'image/png'));
});
