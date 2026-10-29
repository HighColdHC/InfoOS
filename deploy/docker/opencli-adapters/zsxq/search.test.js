import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { normalizeArticleUrl } from './article-script.js';

// Run adapter logic with an injected CLI/page boundary; never load or launch a browser.
function loadAdapter(topics, searchPages = []) {
  const utilitySource = readFileSync(new URL('./utils.js', import.meta.url), 'utf8')
    .replace(/^import[\s\S]*?;\n/gm, '').replace(/^export \{[^}]*\} from [^\n]*\n/gm, '').replace(/^export /gm, '');
  const helpers = vm.runInNewContext(`${utilitySource}\nfetchFirstJson = async (page, paths) => page.fetch(paths);\n({ topicDateBounds, filterAndSortTopics, toTopicRow, fetchTopicPages });`, {
    ArgumentError: Error, AuthRequiredError: Error, CliError: Error, URL, process: { env: process.env }, normalizeArticleUrl,
  });
  const calls = [];
  let definition;
  const source = readFileSync(new URL('./search.js', import.meta.url), 'utf8')
    .replace(/^import[\s\S]*?;\n/gm, '').replace(/^export \{[^}]*\} from [^\n]*\n/gm, '').replace(/^export /gm, '');
  const context = {
    ...helpers, cli: value => { definition = value; }, Strategy: { COOKIE: 'cookie' },
    DEFAULT_GROUP_ID: 'group', createHydrationBudget: () => ({ deadlineMs: Infinity }), ensureZsxqAuth: async () => {}, ensureZsxqPage: async () => {},
    fetchFirstJson: async (_page, urls) => {
      calls.push(urls[0]);
      return { data: { topics: urls[0].includes('/topics?') ? topics : [] } };
    },
    getGroupsFromResponse: () => [], getTopicsFromResponse: data => data.topics,
    getTopicIdentity: topic => topic.topic_uid, getEndTimeFromResponse: () => '',
    topicMatchesAnyKeyword: () => true, validateSearchKeywords: () => {},
    hydrateTopicRows: async (_page, rows) => {
      calls.push('hydrate');
      return rows.map(topic => ({ ...helpers.toTopicRow(topic), full_text: 'hydrated' }));
    },
    fetchTopicPages: async (_page, makeUrl, limit, options) => helpers.fetchTopicPages({
      fetch: async urls => { calls.push(urls[0]); return { data: { topics: searchPages.shift() || [] } }; },
    }, makeUrl, limit, options),
  };
  vm.runInNewContext(`${source}\nthis.getInitialEndTime = getInitialEndTime;`, context);
  return { definition, calls, initialEndTime: context.getInitialEndTime, helpers };
}

const topics = [
  { topic_uid: 'last', create_time: '2026-10-12T23:59:59.500+0800', talk: { text: '列表正文' } },
  { topic_uid: 'next', create_time: '2026-10-13T00:00:00.000+0800', talk: { text: '次日' } },
];
const options = { keyword: '*', 'start-date': '2026-10-12', 'end-date': '2026-10-12',
  'content-type': 'essence', sort: 'newest', limit: 60 };

test('metadata-only search retains digest identity without hydrating external documents', async () => {
  const { definition, calls } = loadAdapter(topics);
  assert.equal(definition.args.find(arg => arg.name === 'hydrate').default, 'true');
  const rows = await definition.func({}, { ...options, hydrate: 'false' });
  assert.deepEqual(Array.from(rows, row => row.topic_id), ['last']);
  assert.equal(rows[0].is_digest, true);
  assert.equal(rows[0].full_text, '列表正文');
  assert.equal(calls.includes('hydrate'), false);
  const requested = new URL(calls.find(url => url.includes('/topics?')));
  assert.equal(requested.searchParams.get('scope'), 'digests');
  assert.equal(Date.parse(requested.searchParams.get('end_time')), Date.parse('2026-10-12T23:59:59.999+08:00'));
});

test('default search still hydrates selected posts', async () => {
  const { definition, calls } = loadAdapter(topics);
  const rows = await definition.func({}, options);
  assert.equal(rows[0].full_text, 'hydrated');
  assert.equal(calls.filter(call => call === 'hydrate').length, 1);
});

test('calendar boundaries compare instants across offsets and include final milliseconds', () => {
  const { helpers, initialEndTime } = loadAdapter([]);
  assert.equal(Date.parse(initialEndTime('2026-10-12')), Date.parse('2026-10-12T23:59:59.999+08:00'));
  const filtered = helpers.filterAndSortTopics([...topics,
    { topic_uid: 'utc', create_time: '2026-10-12T15:59:59.500Z' },
    { topic_uid: 'before', create_time: '2026-10-11T23:59:59.999+0800' },
  ], { startDate: '2026-10-12', endDate: '2026-10-12', sort: 'newest' });
  assert.deepEqual(Array.from(filtered, row => row.topic_uid), ['last', 'utc']);
});

test('chronological pagination stops on the first page crossing the inclusive start date', async () => {
  const { helpers } = loadAdapter([]);
  const pages = [[topics[0], { topic_uid: 'old', create_time: '2026-10-11T23:59:59+0800' }], topics];
  let requests = 0;
  const page = { fetch: async () => { requests += 1; return { data: { topics: pages.shift() } }; } };
  await helpers.fetchTopicPages(page, cursor => `https://api.zsxq.com/?end_time=${cursor}`, 100,
    { startDate: '2026-10-12', chronological: true });
  assert.equal(requests, 1);
});

test('ranked keyword pagination does not stop merely because an old result appears', async () => {
  const { helpers } = loadAdapter([]);
  const pages = [[{ topic_uid: 'old', create_time: '2026-10-11T23:59:59+0800' }], [topics[0]], []];
  let requests = 0;
  const page = { fetch: async () => { requests += 1; return { data: { topics: pages.shift() } }; } };
  const rows = await helpers.fetchTopicPages(page, cursor => `https://api.zsxq.com/?end_time=${cursor}`, 100,
    { startDate: '2026-10-12' });
  assert.equal(requests, 3);
  assert.equal(rows[1].topic_uid, 'last');
});

test('four keywords share 100 candidate slots rather than scanning 500 each', async () => {
  const keywords = ['AI', '工具', '创业', '小红书'];
  const pages = keywords.map((_, index) => Array.from({ length: 25 }, (__, row) => ({
    topic_uid: `${index}-${row}`, create_time: '2026-10-12T12:00:00+0800',
    talk: { text: keywords.join(' ') },
  })));
  const { definition, calls } = loadAdapter([], pages);
  const rows = await definition.func({}, {
    keyword: 'AI', 'keywords-json': JSON.stringify(keywords), 'content-type': 'all',
    'start-date': '2025-07-01', limit: 100, hydrate: 'false',
  });
  const requests = calls.filter(url => url.includes('/search/groups/'));
  assert.equal(requests.length, 4);
  assert.ok(requests.every(url => new URL(url).searchParams.get('count') === '25'));
  assert.equal(rows.length, 100);
});

test('duplicate candidates consume the shared scan budget without extra pages', async () => {
  const duplicate = { topic_uid: 'duplicate', create_time: '2026-10-12T12:00:00+0800', talk: { text: 'AI 工具' } };
  const pages = [Array(30).fill(duplicate), Array(30).fill(duplicate), [duplicate]];
  const { definition, calls } = loadAdapter([], pages);
  const rows = await definition.func({}, { keyword: 'AI', 'keywords-json': '["AI","工具"]',
    'content-type': 'all', limit: 60, hydrate: 'false' });
  assert.equal(calls.filter(url => url.includes('/search/groups/')).length, 2);
  assert.equal(rows.length, 1);
});

test('sparse pages cannot exceed the shared page ceiling', async () => {
  const pages = Array.from({ length: 20 }, (_, index) => [{ topic_uid: String(index),
    create_time: `2026-10-12T12:00:${String(index).padStart(2, '0')}+0800`, talk: { text: 'AI 工具' } }]);
  const { definition, calls } = loadAdapter([], pages);
  await definition.func({}, { keyword: 'AI', 'keywords-json': '["AI","工具"]',
    'content-type': 'all', limit: 100, hydrate: 'false' });
  assert.equal(calls.filter(url => url.includes('/search/groups/')).length, 5);
});

test('last page uses only remaining candidate slots instead of overfetching another 30', async () => {
  const { helpers } = loadAdapter([]);
  const counts = [];
  let id = 0;
  const page = { fetch: async paths => {
    const count = Number(new URL(paths[0]).searchParams.get('count'));
    return { data: { topics: Array.from({ length: count }, () => ({ topic_uid: String(++id),
      create_time: `2026-10-12T12:00:${String(id).padStart(2, '0')}+0800` })) } };
  }};
  const rows = await helpers.fetchTopicPages(page, (_cursor, remaining) => {
    const count = Math.min(remaining, 30);
    counts.push(count);
    return `https://api.zsxq.com/v2/search/groups/g/topics?count=${count}`;
  }, 34, { maxCandidates: 34 });
  assert.deepEqual(counts, [30, 4]);
  assert.equal(rows.length, 34);
});


test('incremental list returns every new post beyond limit and stops at the since instant', async () => {
  const { helpers } = loadAdapter([]);
  const rows = [30, 29, 28, 25, 24].map(day => ({ topic_uid: String(day), create_time: `2026-08-${day}T00:00:00Z` }));
  const pages = [rows.slice(0, 2), rows.slice(2, 4), rows.slice(4)];
  let requests = 0;
  const page = { fetch: async () => { requests += 1; return { data: { topics: pages.shift() } }; } };
  const result = await helpers.fetchTopicPages(page, () => 'https://api.zsxq.com/list', 1,
    { since: '2026-08-26T00:00:00Z', chronological: true });
  assert.deepEqual(Array.from(result, row => row.topic_uid), ['30', '29', '28']);
  assert.equal(requests, 2);
});

test('incremental list stops at default five-page cap while still below limit', async () => {
  const { helpers } = loadAdapter([]);
  let requests = 0;
  const page = { fetch: async () => { requests += 1; return { data: { topics: [
    { topic_uid: String(requests), create_time: `2026-08-${30 - requests}T00:00:00Z` },
  ] } }; } };
  const result = await helpers.fetchTopicPages(page, () => 'https://api.zsxq.com/list', 100,
    { since: '2026-08-01T00:00:00Z', chronological: true });
  assert.equal(requests, 5);
  assert.equal(result.length, 5);
});
