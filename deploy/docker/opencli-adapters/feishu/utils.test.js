import assert from 'node:assert/strict';
import test from 'node:test';
import './document-script.test.js';

import {
  normalizeDocumentPayload,
  parseFeishuDocumentUrl,
  readFeishuDocument,
} from './utils.js';

test('parseFeishuDocumentUrl accepts tenant documents and rejects lookalike hosts', () => {
  assert.equal(
    parseFeishuDocumentUrl('https://tenant.feishu.cn/docx/ABC'),
    'https://tenant.feishu.cn/docx/ABC',
  );
  assert.equal(
    parseFeishuDocumentUrl('https://tenant.larksuite.com/wiki/ABC'),
    'https://tenant.larksuite.com/wiki/ABC',
  );
  assert.throws(
    () => parseFeishuDocumentUrl('https://tenant.feishu.cn.evil.example/docx/ABC'),
    /feishu\.cn or larksuite\.com/,
  );
  assert.throws(
    () => parseFeishuDocumentUrl('http://tenant.feishu.cn/docx/ABC'),
    /HTTPS URL/,
  );
});

test('readFeishuDocument navigates and returns bounded complete document facts', async () => {
  const calls = [];
  const mediaBody = Buffer.from('\x89PNG\r\n\x1a\nimage').toString('base64');
  const page = {
    goto: async (url) => calls.push(['goto', url]),
    evaluate: async (script) => {
      calls.push(['evaluate', script]);
      if (script.startsWith('new Promise')) return undefined;
      return {
        session: 'feishu',
        data: {
          url: 'https://tenant.feishu.cn/docx/ABC',
          title: '文档标题',
          body: '完整飞书正文',
          complete: true, completeness: 'complete', blocks: 20, reached_end: true,
          login_required: false,
          images: ['https://tenant.feishu.cn/image/1', 'javascript:alert(1)'],
          videos: ['https://tenant.feishu.cn/video/1'],
          media_blobs: [
            {
              url: 'https://internal-api-drive-stream.feishu.cn/image/1',
              mime_type: 'image/png',
              base64: mediaBody,
            },
          ],
        },
      };
    },
  };

  const row = await readFeishuDocument(page, 'https://tenant.feishu.cn/docx/ABC');

  assert.equal(row.title, '文档标题');
  assert.equal(row.body, '完整飞书正文');
  assert.deepEqual(row.images, ['https://tenant.feishu.cn/image/1']);
  assert.deepEqual(row.videos, ['https://tenant.feishu.cn/video/1']);
  assert.deepEqual(row.media_blobs, [
    {
      url: 'https://internal-api-drive-stream.feishu.cn/image/1',
      mime_type: 'image/png',
      base64: mediaBody,
    },
  ]);
  assert.equal(calls[0][0], 'goto');
  const documentScript = calls.at(-1)[1];
  assert.match(documentScript, /credentials:\s*['"]include['"]/);
  assert.match(documentScript, /isTrustedMedia/);
  assert.match(documentScript, /getRowCount/);
  assert.match(documentScript, /getColumnCount/);
  assert.match(documentScript, /getText/);
  assert.match(documentScript, /Math\.min\(deadline, Date\.now\(\) \+ 30000\)/);
  assert.equal(row.complete, true);
  assert.equal(row.blocks, 20);
  assert.match(documentScript, /Math\.min\(15000, mediaDeadline/);
  assert.doesNotMatch(
    documentScript.toLowerCase(),
    /document\.cookie|localstorage|sessionstorage|clipboard/,
  );
});

test('normalizeDocumentPayload caps body and media lists', () => {
  const row = normalizeDocumentPayload(
    {
      body: 'x'.repeat(130_000),
      images: Array.from({ length: 250 }, (_, index) => `https://cdn.example/${index}`),
    },
    'https://tenant.feishu.cn/docx/ABC',
  );

  assert.equal(row.body.length, 120_000);
  assert.equal(row.images.length, 200);
});

test('normalizeDocumentPayload bounds authenticated media and rejects untrusted blobs', () => {
  const validBase64 = Buffer.from('\x89PNG\r\n\x1a\nimage').toString('base64');
  const mediaBlobs = Array.from({ length: 22 }, (_, index) => ({
    url: `https://internal-api-drive-stream.feishu.cn/image/${index}`,
    mime_type: 'image/png',
    base64: validBase64,
  }));
  mediaBlobs.splice(1, 0, {
    url: 'https://evil.example/image/1',
    mime_type: 'image/png',
    base64: validBase64,
  });
  mediaBlobs.splice(2, 0, {
    url: 'https://internal-api-drive-stream.feishu.cn/image/invalid',
    mime_type: 'text/html',
    base64: validBase64,
  });

  const row = normalizeDocumentPayload(
    { media_blobs: mediaBlobs },
    'https://tenant.feishu.cn/docx/ABC',
  );

  assert.equal(row.media_blobs.length, 20);
  assert.ok(row.media_blobs.every((blob) => blob.url.endsWith('.feishu.cn/image/' + blob.url.split('/').at(-1))));
  assert.ok(row.media_blobs.every((blob) => blob.mime_type === 'image/png'));
});

test('normalizeDocumentPayload treats missing completeness and truncated content as partial', () => {
  for (const payload of [{ body: '未知完整性' }, { body: '长'.repeat(120001), complete: true },
    { body: '登录', complete: true, login_required: true }, { body: '部分', complete: true, completeness: 'partial' }]) {
    assert.equal(normalizeDocumentPayload(payload, 'https://tenant.feishu.cn/docx/ABC').complete, false);
  }
});
