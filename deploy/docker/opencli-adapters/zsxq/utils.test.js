import assert from 'node:assert/strict';
import test from 'node:test';
import './document-hydration.test.js';
import './request-control.test.js';
import { createZsxqRequestControl } from './request-control.js';

// All registered-command browser doubles run with zero real pacing delays.
const timingEnvironment = ['INFOOS_ZSXQ_REQUEST_INTERVAL_MS', 'INFOOS_ZSXQ_REQUEST_JITTER_MS', 'INFOOS_ZSXQ_1059_COOLDOWN_MS'];
const previousTiming = timingEnvironment.map(name => process.env[name]);
for (const name of timingEnvironment) process.env[name] = '0';
test.after(() => timingEnvironment.forEach((name, index) => {
  if (previousTiming[index] === undefined) delete process.env[name];
  else process.env[name] = previousTiming[index];
}));

import {
  browserJsonRequest,
  filterAndSortTopics,
  topicDateBounds,
  extractTopicLinks,
  getTopicContent,
  getTopicIdentity,
  getTopicTags,
  hasWindVaneTag,
  hydrateTopicRow,
  hydrateTopicRows,
  createHydrationBudget,
  toTopicRow,
} from './utils.js';

test('extracts hashtag labels from ZSXQ rich text and accepts 风向标 variants', () => {
  const topic = {
    topic_uid: '90000000000000002',
    talk: { text: '<e type="hashtag" title="%E9%A3%8E%E5%90%91%E6%A0%87" /> 一个真实机会' },
  };
  assert.deepEqual(getTopicTags(topic), ['风向标']);
  assert.equal(hasWindVaneTag(topic), true);
  assert.equal(hasWindVaneTag({ topic_uid: '1', talk: { text: '#AI 风向标# 机会' } }), true);
  assert.equal(hasWindVaneTag({ topic_uid: '2', talk: { text: '#风向# 机会' } }), false);
});

test('classifies extensionless images.zsxq.com media as native images', () => {
  const row = toTopicRow({
    topic_uid: '123',
    talk: { text: '正文', images: [{ original: { url: 'https://images.zsxq.com/Fnabc123?imageMogr2/auto-orient' } }] },
  });
  assert.deepEqual(row.images, ['https://images.zsxq.com/Fnabc123?imageMogr2/auto-orient']);
});

test('signs API requests with the same headers as the ZSXQ dweb client', async () => {
  const scripts = [];
  const page = {
    evaluate: async (script) => {
      scripts.push(script);
      if (scripts.length === 1) return 'device-id';
      return { ok: true, status: 200, data: { succeeded: true } };
    },
  };

  const result = await browserJsonRequest(page, 'https://api.zsxq.com/v2/topics/123');

  assert.equal(result.ok, true);
  assert.match(scripts[1], /X-Aduid/);
  assert.match(scripts[1], /X-Request-Id/);
  assert.match(scripts[1], /X-Signature/);
  assert.match(scripts[1], /X-Timestamp/);
  assert.match(scripts[1], /X-Version/);
  assert.match(scripts[1], /2\.95\.0/);
});

test('retries 1059 after natural cooling while retaining the same device identity', async () => {
  const scripts = [];
  const sleeps = [];
  let now = 0;
  let requests = 0;
  const control = createZsxqRequestControl({ env: {}, now: () => now,
    sleep: async ms => { sleeps.push(ms); now += ms; }, random: () => 0 });
  const page = { evaluate: async script => {
    scripts.push(script);
    if (!script.includes('const path =')) return 'device-id';
    requests += 1;
    return { ok: true, data: requests === 1 ? { succeeded: false, code: 1059 } : { succeeded: true } };
  }};
  const result = await browserJsonRequest(page, 'https://api.zsxq.com/v2/topics/123', control);
  assert.equal(result.data.succeeded, true);
  assert.deepEqual(sleeps, [20000]);
  const signed = scripts.filter(script => script.includes('const headers ='));
  assert.equal(signed.length, 2);
  assert.ok(signed.every(script => script.includes('"X-Aduid":"device-id"')));
});

test('auth probe preserves RATE_LIMITED rather than converting it to login failure', async () => {
  const { ensureZsxqAuth } = await import('./utils.js');
  const error = Object.assign(new Error('zsxq risk control 1059 (rate limited)'), { code: 'RATE_LIMITED' });
  await assert.rejects(ensureZsxqAuth({ evaluate: async () => { throw error; } }), e => e === error || e.code === 'RATE_LIMITED');
});

test('extracts and decodes ZSXQ rich-text links', () => {
  const topic = {
    topic_id: '123',
    talk: {
      text: '全文：<e type="web" title="%E9%A3%9E%E4%B9%A6%E6%96%87%E6%A1%A3" href="https%3A%2F%2Ftenant.feishu.cn%2Fdocx%2FABC" style="book" />',
    },
  };

  assert.deepEqual(extractTopicLinks(topic), ['https://tenant.feishu.cn/docx/ABC']);
  assert.equal(getTopicContent(topic), '全文：飞书文档');
  assert.deepEqual(toTopicRow(topic).links, ['https://tenant.feishu.cn/docx/ABC']);
});

test('uses the lossless topic_uid instead of an unsafe numeric topic_id', () => {
  const row = toTopicRow({
    topic_uid: '90000000000000002',
    topic_id: 90000000000000001,
    talk: { text: '正文' },
  });

  assert.equal(row.topic_id, '90000000000000002');
  assert.equal(row.legacy_topic_id, 90000000000000001);
  assert.equal(row.url, 'https://wx.zsxq.com/topic/90000000000000002');
  assert.equal(getTopicIdentity({ topic_uid: '90000000000000002', topic_id: 1 }), '90000000000000002');
});

test('retains the ZSXQ original before the complete Feishu document body', async () => {
  const calls = [];
  const page = {
    goto: async (url) => calls.push(['goto', url]),
    evaluate: async (script) => {
      calls.push(['evaluate', script]);
      if (script.startsWith('new Promise')) return undefined;
      return {
        url: 'https://tenant.feishu.cn/docx/ABC',
        title: '完整文章',
        body: '这才是真正的帖子正文。',
        complete: true, completeness: 'complete', reached_end: true, blocks: 20,
        login_required: false,
        images: [],
        videos: [],
        media_blobs: [],
      };
    },
  };
  const row = await hydrateTopicRow(page, {
    topic_id: '123',
    talk: {
      text: '原帖只有跳转提示 <e type="web" title="阅读全文" href="https%3A%2F%2Ftenant.feishu.cn%2Fdocx%2FABC" />',
    },
  });

  assert.equal(row.content, '原帖只有跳转提示 阅读全文\n\n这才是真正的帖子正文。');
  assert.equal(row.original_content, '原帖只有跳转提示 阅读全文');
  assert.equal(row.external_doc_body, '这才是真正的帖子正文。');
  assert.equal(row.full_text, row.content);
  assert.equal(row.content_source, 'feishu');
  assert.equal(row.content_source_status, 'fetched');
  assert.equal(row.content_source_url, 'https://tenant.feishu.cn/docx/ABC');
  assert.equal(row.url, 'https://wx.zsxq.com/topic/123');
  assert.equal(calls[0][0], 'goto');
});

test('retains the ZSXQ original when Feishu needs login', async () => {
  const page = {
    goto: async () => undefined,
    evaluate: async (script) => script.startsWith('new Promise') ? undefined : {
      url: 'https://tenant.feishu.cn/docx/ABC',
      title: '',
      body: '登录页面文案',
      login_required: true,
      images: [],
      videos: [],
      media_blobs: [],
    },
  };
  const row = await hydrateTopicRow(page, {
    topic_id: '123',
    talk: {
      text: '应保留的原帖内容 <e type="web" title="飞书" href="https%3A%2F%2Ftenant.feishu.cn%2Fdocx%2FABC" />',
    },
  });

  assert.equal(row.content, '应保留的原帖内容 飞书');
  assert.equal(row.full_text, row.content);
  assert.equal(row.external_doc_body, '');
  assert.equal(row.content_source_status, 'login_required');
});

// Registered adapter commands are exercised with a fake browser only.
import { getRegistry } from '@jackwener/opencli/registry';
import { isDigestTopic, topicMatchesContentType, topicMatchesAnyKeyword, validateSearchKeywords } from './utils.js';
import { fetchListedTopics, getInitialEndTime } from './search.js';
import './topics.js';
import './opportunity.js';

for (const topic of [{ is_digest: true }, { is_digested: true }, { digested: true },
  { digest_time: '2026-09-01' }, { tags: ['精华'] }]) {
  test(`digest predicate branch ${JSON.stringify(topic)}`, () => {
    assert.equal(isDigestTopic(topic), true);
    assert.equal(topicMatchesContentType(topic, 'essence'), true);
    assert.equal(toTopicRow(topic).is_digest, true);
  });
}
test('digest predicate rejects absent and false flags', () => {
  assert.equal(isDigestTopic({}), false);
  assert.equal(isDigestTopic({ is_digest: false, tags: ['风向标'] }), false);
});
test('wildcard types and bounded start date', () => {
  for (const type of ['all', 'essence']) validateSearchKeywords([' * ', 'AI'], type, '2026-08-26');
  for (const type of ['bid', 'topic', 'opportunity', 'square']) {
    assert.throws(() => validateSearchKeywords(['*'], type, '2026-08-26'), /all or essence/);
  }
  assert.throws(() => validateSearchKeywords(['*'], 'all'), /start-date/);
  assert.equal(topicMatchesAnyKeyword({}, ['AI', ' * ']), true);
  assert.equal(topicMatchesAnyKeyword({ talk: { text: 'AI 方法' } }, ['ai']), true);
  assert.equal(topicMatchesAnyKeyword({ talk: { text: '其他' } }, ['AI']), false);
});
function fakePages(pages, menus = { menus: [{ title: '风向标', hashtag: { hashtag_id: 'wind' } }] }) {
  const urls = [];
  return { urls, goto: async () => {}, evaluate: async (script) => {
    if (!script.includes('const path =')) return 'device';
    const url = JSON.parse(script.match(/const path = (.*);/)[1]);
    urls.push(url);
    if (url.endsWith('/groups')) return { ok: true, data: { resp_data: { groups: [] } } };
    if (url.endsWith('/menus')) return { ok: true, data: { resp_data: menus } };
    return { ok: true, data: { resp_data: { topics: pages.shift() || [] } } };
  }};
}

test('registered search propagates group-name 1059 rather than continuing to topic requests', async () => {
  let groups = 0;
  const page = { goto: async () => {}, evaluate: async script => {
    if (!script.includes('const path =')) return 'device';
    const url = JSON.parse(script.match(/const path = (.*);/)[1]);
    assert.ok(url.endsWith('/groups'), 'topic requests must stop after group-name risk control');
    groups += 1;
    return { ok: true, data: groups === 1
      ? { succeeded: true, resp_data: { groups: [] } }
      : { succeeded: false, code: 1059 } };
  }};
  await assert.rejects(getRegistry().get('zsxq/search').func(page, {
    keyword: 'AI', 'content-type': 'all', limit: 1,
  }), error => error.code === 'RATE_LIMITED' && /1059/.test(error.message));
  assert.equal(groups, 3);
});
const topic = (id, time, extra = {}) => ({ topic_uid: id, create_time: `2026-08-${time}T12:00:00+08:00`, talk: { text: 'AI 正文' }, ...extra });
test('initial end_time uses the requested date with milliseconds and compact timezone', () => {
  assert.equal(getInitialEndTime('2026-09-01'), '2026-09-01T23:59:59.999+0800');
  for (const date of [undefined, null, '', 20260901, '2026-9-01', '2026/09/01', '2026-09-01\n', '2026-09-01T12:00:00']) {
    assert.equal(getInitialEndTime(date), '');
  }
});
for (const endDate of ['2026-09-01', undefined, '2026/09/01']) {
  test(`listed topics first request cursor for endDate=${endDate}`, async () => {
    const page = fakePages([[]]);
    const request = () => fetchListedTopics(page, '1824528822', {
      keywords: ['*'], startDate: '2026-08-26', endDate, sort: 'earliest', limit: 100,
    });
    if (endDate === '2026/09/01') {
      await assert.rejects(request(), /Date must use YYYY-MM-DD/);
      return;
    }
    await request();
    assert.equal(page.urls.length, 1);
    if (endDate === '2026-09-01') {
      assert.ok(page.urls[0].includes(`&end_time=${encodeURIComponent('2026-09-01T23:59:59.999+0800')}`));
      assert.equal(new URL(page.urls[0]).searchParams.get('end_time'), '2026-09-01T23:59:59.999+0800');
    } else {
      assert.equal(new URL(page.urls[0]).searchParams.has('end_time'), false);
    }
  });
}
test('a one-page date window needs only two requests and stops below startDate', async () => {
  const inWindow = [
    topic('30', '30', { create_time: '2026-08-30T12:00:00.647+0800' }),
    topic('27', '27', { create_time: '2026-08-27T12:00:00.647+0800' }),
  ];
  const page = fakePages([inWindow, [topic('25', '25')], [topic('24', '24')]]);
  const topics = await fetchListedTopics(page, '1824528822', {
    keywords: ['*'], startDate: '2026-08-26', endDate: '2026-09-01', sort: 'earliest', limit: 100,
    scope: 'all',
  });
  assert.deepEqual(topics, inWindow);
  assert.equal(page.urls.length, 2);
  assert.equal(new URL(page.urls[1]).searchParams.get('end_time'), inWindow[1].create_time);
});
test('listed topics return collected posts after at most forty pages', async () => {
  const pages = Array.from({ length: 41 }, (_, index) => [topic(String(index + 1), '30', {
    create_time: `2026-08-30T12:00:${String(59 - index).padStart(2, '0')}.647+0800`,
  })]);
  const expected = pages.slice(0, 40).flat();
  const page = fakePages(pages);
  const topics = await fetchListedTopics(page, '1824528822', {
    keywords: ['*'], startDate: '2026-08-26', endDate: '2026-09-01', sort: 'earliest', limit: 100,
    scope: 'all',
  });
  assert.equal(page.urls.length, 40);
  assert.deepEqual(topics, expected);
});
for (const type of ['all', 'essence']) {
  test(`wildcard ${type} uses list scope and earliest scans complete range`, async () => {
    const page = fakePages([[topic('30', '30')], [topic('27', '27')], [topic('25', '25')]]);
    const rows = await getRegistry().get('zsxq/search').func(page, {
      keyword: '*', 'content-type': type, 'start-date': '2026-08-26', 'end-date': '2026-09-01', sort: 'earliest', limit: 1,
    });
    assert.deepEqual(rows.map(row => row.topic_id), ['27']);
    const lists = page.urls.filter(url => url.includes('/topics?'));
    assert.equal(lists.length, 3);
    assert.ok(lists.every(url => url.includes(`scope=${type === 'all' ? 'all' : 'digests'}`) && !url.includes('/search/')));
    assert.equal(rows[0].is_digest, type === 'essence');
  });
}
for (const sort of ['comprehensive', 'newest']) {
  test(`${sort} stops at limit`, async () => {
    const page = fakePages([[topic('30', '30')], [topic('27', '27')]]);
    const rows = await getRegistry().get('zsxq/search').func(page, {
      keyword: '*', 'content-type': 'all', 'start-date': '2026-08-26', sort, limit: 1,
    });
    assert.equal(rows.length, 1);
    assert.equal(page.urls.filter(url => url.includes('/topics?')).length, 1);
  });
}
test('ordinary keyword keeps search endpoint and digest field', async () => {
  const page = fakePages([[topic('30', '30', { is_digested: true })]]);
  const rows = await getRegistry().get('zsxq/search').func(page, { keyword: 'AI', 'content-type': 'all', limit: 1 });
  assert.ok(page.urls.some(url => url.includes('/search/groups/') && url.includes('keyword=AI')));
  assert.equal(rows[0].is_digest, true);
});
test('topics digest scope forces true without provider flag', async () => {
  const page = fakePages([[topic('30', '30')]]);
  const rows = await getRegistry().get('zsxq/topics').func(page, { filter: 'essence', limit: 1 });
  assert.equal(rows[0].is_digest, true);
});
test('opportunity rows expose inferred digest status', async () => {
  const page = fakePages([[topic('30', '30', { tags: ['风向标', '精华'] })]]);
  const rows = await getRegistry().get('zsxq/opportunity').func(page, { limit: 1 });
  assert.equal(rows[0].is_digest, true);
});

test('opportunity reads a deduplicated menu hashtag and fills limit across pages', async () => {
  const page = fakePages([
    [topic('30', '30')],
    [topic('29', '29'), topic('28', '28')],
  ], { menus: [
    { title: '风向标', preset_type: 'custom' },
    { title: '精华', hashtag: { hashtag_id: 'digest' } },
    { title: ' # 风向标 # ', hashtag: { hashtag_id: 42 } },
  ], optional_menus: [{ title: '#风向标#', hashtag: { hashtag_id: '42' } }] });
  const rows = await getRegistry().get('zsxq/opportunity').func(page, { group_id: '42', limit: 2 });
  assert.deepEqual(rows.map(row => row.topic_id), ['30', '29']);
  assert.equal(page.urls[1], 'https://api.zsxq.com/v2/groups/42/menus');
  const requests = page.urls.slice(2).map(url => new URL(url));
  assert.equal(requests.length, 2);
  for (const url of requests) {
    assert.equal(url.pathname, '/v2/hashtags/42/topics');
    assert.equal(url.searchParams.get('count'), '30');
  }
  assert.equal(requests[0].searchParams.get('end_time'), null);
  assert.equal(requests[1].searchParams.get('end_time'), topic('30', '30').create_time);
});

test('opportunity combines matching menus and optional menus in the multi-tag endpoint', async () => {
  const page = fakePages([[topic('30', '30')], [topic('29', '29')]], {
    menus: [{ title: '#风向标#', hashtag: { hashtag_id: 'wind' } }],
    optional_menus: [
      { title: ' # AI 风向标 # ', hashtag: { hashtag_id: 'ai' } },
      { title: '风向标', hashtag: { hashtag_id: 'wind' } },
    ],
  });
  const rows = await getRegistry().get('zsxq/opportunity').func(page, { limit: 2 });
  assert.deepEqual(rows.map(row => row.topic_id), ['30', '29']);
  const requests = page.urls.slice(2).map(url => new URL(url));
  assert.equal(requests.length, 2);
  for (const url of requests) {
    assert.equal(url.pathname, '/v2/hashtags/topics');
    assert.equal(url.searchParams.get('count'), '30');
    assert.equal(url.searchParams.get('tag_ids'), 'wind,ai');
  }
  assert.equal(requests[1].searchParams.get('end_time'), topic('30', '30').create_time);
});

for (const menus of [{ menus: [] }, {
  menus: [{ title: '风向标' }, { title: '精华', hashtag: { hashtag_id: 'digest' } }],
  optional_menus: [{ title: '#AI 风向标#' }],
}]) {
  test(`opportunity reports missing menu hashtags: ${JSON.stringify(menus)}`, async () => {
    const page = fakePages([], menus);
    await assert.rejects(getRegistry().get('zsxq/opportunity').func(page, { limit: 1 }),
      /未在群菜单中找到风向标标签/);
    assert.deepEqual(page.urls, [
      'https://api.zsxq.com/v2/groups',
      'https://api.zsxq.com/v2/groups/1824528822/menus',
    ]);
  });
}

test('opportunity retains the client-side keyword filter', async () => {
  const page = fakePages([[
    topic('30', '30', { tags: ['风向标'] }),
    topic('29', '29', { tags: ['风向标'], talk: { text: '其他正文' } }),
  ]]);
  const rows = await getRegistry().get('zsxq/opportunity').func(page, { limit: 2, keyword: 'AI' });
  assert.deepEqual(rows.map(row => row.topic_id), ['30']);
});

for (const [body, expected] of [['首屏', 'partial'], ['', 'empty']]) {
  test(`preserves original text for ${expected} Feishu results`, async () => {
    const page = { goto: async () => {}, evaluate: async () => ({ body, complete: false }) };
    const row = await hydrateTopicRow(page, { topic_id: '123', talk: { text: '原帖 https://tenant.feishu.cn/docx/ABC' } });
    assert.equal(row.content_source_status, expected);
    assert.ok(row.content.startsWith('原帖 '));
    assert.equal(row.content_source_complete, false);
    assert.equal(row.external_doc_body, body);
  });
}

test('exhausted list budget marks remaining documents partial without browser calls', async () => {
  const page = { goto: async () => { throw new Error('must not navigate'); } };
  const rows = await hydrateTopicRows(page, [1, 2].map((id) => ({
    topic_id: String(id), talk: { text: `原帖${id} https://tenant.feishu.cn/docx/ABC` },
  })), { deadlineMs: 0 });
  assert.ok(rows.every((row) => row.content_source_status === 'partial' && row.content.includes('原帖')));
  const now = Date.now();
  assert.ok(createHydrationBudget(20).deadlineMs - now >= 540000);
  assert.ok(createHydrationBudget(1).deadlineMs - now >= 40000);
});

test('Shanghai date window includes final milliseconds and excludes next midnight', () => {
  const topics = [
    { topic_uid: 'before', create_time: '2026-10-05T23:59:59.999+0800' },
    { topic_uid: 'start', create_time: '2026-10-06T00:00:00.000+0800' },
    { topic_uid: 'last', create_time: '2026-10-12T23:59:59.500+0800' },
    { topic_uid: 'next', create_time: '2026-10-13T00:00:00.000+0800' },
    { topic_uid: 'utc', create_time: '2026-10-12T15:59:59.500Z' },
    { topic_uid: 'invalid', create_time: 'invalid' },
  ];
  assert.deepEqual(filterAndSortTopics(topics, {
    startDate: '2026-10-06', endDate: '2026-10-12', sort: 'newest',
  }).map(topic => topic.topic_uid), ['last', 'utc', 'start']);
  assert.equal(topicDateBounds('', '2026-10-12').end, Date.parse('2026-10-13T00:00:00+08:00'));
  assert.throws(() => topicDateBounds('2026-02-30', ''), /Invalid calendar date/);
});

for (const command of ['topics', 'opportunity']) {
  test(`${command} --since stops at older page, returns all newer posts beyond limit`, async () => {
    const page = fakePages([
      [topic('30', '30', { tags: ['风向标'] }), topic('29', '29', { tags: ['风向标'] })],
      [topic('28', '28', { tags: ['风向标'] }), topic('25', '25', { tags: ['风向标'] })],
      [topic('24', '24', { tags: ['风向标'] })],
    ]);
    const rows = await getRegistry().get(`zsxq/${command}`).func(page, {
      group_id: '42', limit: 1, since: '2026-08-26T00:00:00Z',
    });
    assert.deepEqual(rows.map(row => row.topic_id), ['30', '29', '28']);
    assert.equal(page.urls.filter(url => url.includes('/topics?')).length, 2);
    assert.ok(page.urls.filter(url => url.includes('/topics?')).every(url => url.includes('count=30')));
  });
  test(`${command} incremental default cap stops at five pages even below limit`, async () => {
    const page = fakePages(Array.from({ length: 8 }, (_, i) => [topic(String(30-i), String(30-i), { tags: ['风向标'] })]));
    const rows = await getRegistry().get(`zsxq/${command}`).func(page, {
      limit: 100, since: '2026-08-01T00:00:00Z',
    });
    assert.equal(rows.length, 5);
    assert.equal(page.urls.filter(url => url.includes('/topics?')).length, 5);
  });
}
test('incremental page cap honors environment and excludes the since boundary', async () => {
  const previous = process.env.INFOOS_ZSXQ_INCREMENTAL_MAX_PAGES;
  process.env.INFOOS_ZSXQ_INCREMENTAL_MAX_PAGES = '2';
  try {
    const opportunities = fakePages(Array.from({ length: 3 }, (_, index) => [
      topic(String(30 - index), String(30 - index), { tags: ['风向标'] }),
      topic(String(30 - index) + '-second', String(30 - index), { tags: ['风向标'] }),
    ]));
    const opportunityRows = await getRegistry().get('zsxq/opportunity').func(opportunities, {
      limit: 1, since: '2026-08-26T00:00:00Z',
    });
    assert.deepEqual(opportunityRows.map(row => row.topic_id), ['30', '30-second', '29', '29-second']);
    assert.equal(opportunities.urls.filter(url => url.includes('/topics?')).length, 2);
    const page = fakePages([[topic('30', '30')], [topic('29', '29')], [topic('28', '28')]]);
    const rows = await getRegistry().get('zsxq/topics').func(page, { limit: 100, since: '2026-08-01T00:00:00Z' });
    assert.equal(rows.length, 2);
    assert.equal(page.urls.filter(url => url.includes('/topics?')).length, 2);
    const boundary = fakePages([[topic('30', '30'), topic('29', '29')]]);
    const filtered = await getRegistry().get('zsxq/topics').func(boundary, { limit: 100, since: topic('29', '29').create_time });
    assert.deepEqual(filtered.map(row => row.topic_id), ['30']);
    const opportunityBoundary = fakePages([[topic('30', '30'), topic('29', '29')]]);
    const opportunityFiltered = await getRegistry().get('zsxq/opportunity').func(opportunityBoundary, {
      limit: 1, since: topic('29', '29').create_time,
    });
    assert.deepEqual(opportunityFiltered.map(row => row.topic_id), ['30']);
  } finally {
    if (previous === undefined) delete process.env.INFOOS_ZSXQ_INCREMENTAL_MAX_PAGES;
    else process.env.INFOOS_ZSXQ_INCREMENTAL_MAX_PAGES = previous;
  }
});

test('topic rows expose canonical article identity and remain partial without reading the article', async () => {
  const primary = 'https://articles.zsxq.com/inline_form/id_first.html';
  const fallback = 'https://articles.zsxq.com/id_second.html';
  const topic = { topic_uid: '123', talk: { text: '截断预览 ...', article: {
    article_url: primary, inline_article_url: fallback, title: '文章标题',
  } } };
  const row = toTopicRow(topic);
  assert.equal(row.article_url, 'https://articles.zsxq.com/id_first.html');
  assert.equal(row.article_title, '文章标题');
  assert.equal(row.content_source_status, 'partial');
  assert.equal(row.content_source_completeness, 'partial');
  assert.equal(toTopicRow({ talk: { article: { inline_article_url: fallback } } }).article_url, fallback);
  assert.equal(toTopicRow({ links: [primary] }).article_url, row.article_url);
  const hydrated = await hydrateTopicRow({ goto: () => { throw new Error('unexpected browser navigation'); } },
    { ...topic, links: ['https://my.feishu.cn/docx/abc'] });
  assert.equal(hydrated.content, '截断预览 ...');
});
