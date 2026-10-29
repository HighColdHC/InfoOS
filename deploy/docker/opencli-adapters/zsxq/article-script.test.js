import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { collectArticle, normalizeArticleUrl } from './article-script.js';

test('canonicalizes inline article URLs and restricts the article origin', () => {
  assert.equal(normalizeArticleUrl('https://articles.zsxq.com/inline_form/id_abc123.html?x=1'),
    'https://articles.zsxq.com/id_abc123.html');
  assert.equal(normalizeArticleUrl('https://articles.zsxq.com/id_abc123.html'),
    'https://articles.zsxq.com/id_abc123.html');
  for (const url of ['http://articles.zsxq.com/id_a.html', 'https://articles.zsxq.com.evil/id_a.html',
    'https://user@articles.zsxq.com/id_a.html', 'https://articles.zsxq.com/other']) {
    assert.equal(normalizeArticleUrl(url), '');
  }
});

function collect({ body = '', login = false, limit = 120000 } = {}) {
  const editor = { innerText: body, querySelectorAll: (selector) => selector === 'img'
    ? [{ src: 'https://article-images.zsxq.com/a' }, { src: 'https://article-images.zsxq.com/a' }]
    : [{ href: 'https://my.feishu.cn/docx/abc' }, { href: 'javascript:alert(1)' }] };
  const document = { title: '标题\ufeff', body: { innerText: login ? '请扫码登录' : '' },
    querySelector: (selector) => selector === '.ql-editor' ? editor
      : selector.startsWith('h1') ? null : login ? {} : null };
  return vm.runInNewContext(`(${collectArticle.toString()})({budgetMs: 0, maxBodyChars: ${limit}})`,
    { document, location: { href: 'https://articles.zsxq.com/id_a.html' }, URL, Date });
}

test('extracts editor body, title, images and document links with zero-width characters cleaned', async () => {
  const result = await collect({ body: '文章\u200b\u200c\u200d\u2060\ufeff正文' });
  assert.equal(result.status, 'fetched');
  assert.equal(result.title, '标题');
  assert.equal(result.body, '文章正文');
  assert.equal(result.complete, true);
  assert.deepEqual(Array.from(result.images), ['https://article-images.zsxq.com/a']);
  assert.deepEqual(Array.from(result.links), ['https://my.feishu.cn/docx/abc']);
});

test('reports empty and login pages without claiming document text', async () => {
  assert.equal((await collect()).status, 'empty');
  const result = await collect({ login: true });
  assert.equal(result.status, 'login_required');
  assert.equal(result.body, '');
});

test('bounds article text and reports incomplete extraction at the limit', async () => {
  const result = await collect({ body: '正文'.repeat(10), limit: 5 });
  assert.equal(result.body.length, 5);
  assert.equal(result.complete, false);
});
