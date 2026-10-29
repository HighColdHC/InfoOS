import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { parseFilters, planFilters, applySearchFilters, SORT_LABELS, PUBLISH_LABELS } from './_search_filters.js';

test('defaults preserve upstream path without evaluate/hover/click', async () => {
  assert.deepEqual(parseFilters({}), { sort: 'general', publish_time: 'any' });
  assert.deepEqual(planFilters(parseFilters({})), []);
  await applySearchFilters({}, parseFilters({}));
});

test('every canonical sort and publish-time maps to the exact webpage label', () => {
  for (const [sort, label] of Object.entries(SORT_LABELS)) {
    const plan = planFilters(parseFilters({ sort }));
    assert.equal(plan.length, sort === 'general' ? 0 : 1);
    if (plan.length) assert.equal(plan[0].label, label);
  }
  for (const [value, label] of Object.entries(PUBLISH_LABELS)) {
    const plan = planFilters(parseFilters({ 'publish-time': value }));
    assert.equal(plan.length, value === 'any' ? 0 : 1);
    if (plan.length) assert.equal(plan[0].label, label);
  }
  assert.deepEqual(planFilters(parseFilters({ sort: 'latest', 'publish-time': 'week' })).map(item => item.group), ['排序依据', '发布时间']);
});

test('invalid arguments are rejected', () => {
  for (const sort of ['invalid', '', 'LATEST', 1, '__proto__']) assert.throws(() => parseFilters({ sort }), TypeError);
  for (const value of ['month', '', 1]) assert.throws(() => parseFilters({ 'publish-time': value }), TypeError);
});

test('nondefault filters require real hover and fail closed', async () => {
  await assert.rejects(applySearchFilters({}, parseFilters({ sort: 'latest' })), /page.hover is unavailable/);
  await assert.rejects(applySearchFilters({ hover: async () => {}, evaluate: async () => ({ found: false }) }, parseFilters({ sort: 'latest' })), /entry not found/);
});

test('real hover/click sequence applies both filters and skips selected options', async () => {
  const calls = [];
  let selected = false;
  const page = {
    hover: async selector => calls.push(['hover', selector]),
    click: async selector => calls.push(['click', selector]),
    evaluate: async script => {
      const action = script.match(/\},"(entry|panel|option|before|after)",/)[1];
      calls.push([action]);
      return { session: 'test', data: { found: true, selected } };
    },
  };
  const options = parseFilters({ sort: 'latest', 'publish-time': 'day' });
  await applySearchFilters(page, options);
  assert.equal(calls.filter(([action]) => action === 'hover').length, 1);
  assert.equal(calls.filter(([action]) => action === 'click').length, 2);
  assert.deepEqual(calls.map(([action]) => action), ['entry', 'hover', 'panel', 'panel', 'option', 'before', 'click', 'after', 'panel', 'option', 'before', 'click', 'after']);
  selected = true;
  calls.length = 0;
  await applySearchFilters(page, options);
  assert.equal(calls.filter(([action]) => action === 'click').length, 0);
});

test('auth walls and click failures retain the failing step', async () => {
  await assert.rejects(applySearchFilters({ hover: async () => {}, evaluate: async () => ({ auth: true }) }, parseFilters({ sort: 'latest' })), error => error.auth === true);
  await assert.rejects(applySearchFilters({ hover: async () => {}, evaluate: async () => ({ found: true }), click: async () => { throw Error('bridge failed'); } }, parseFilters({ sort: 'latest' })), /click option.*bridge failed/);
});


test('panel hover retries once and refresh without a new request fails closed', async t => {
  let now = 0;
  t.mock.method(Date, 'now', () => now);
  const options = parseFilters({ sort: 'latest' });
  let hovers = 0;
  const page = {
    hover: async () => { hovers++; }, click: async () => {},
    evaluate: async script => {
      if (script.startsWith('new Promise')) { now += 1000; return; }
      const action = script.match(/\},"(entry|panel|option|before|after)",/)[1];
      return { found: action === 'panel' ? hovers === 2 : action !== 'after' };
    },
  };
  await assert.rejects(applySearchFilters(page, options), /wait search refresh.*10s/);
  assert.equal(hovers, 2);
  hovers = 0;
  page.evaluate = async script => {
    if (script.startsWith('new Promise')) { now += 1000; return; }
    return { found: script.includes(',"entry",') };
  };
  await assert.rejects(applySearchFilters(page, options), /wait filter panel.*6s/);
  assert.equal(hovers, 2);
});

test('a hard navigation after the click is tolerated only when the option stays selected', async () => {
  const options = parseFilters({ sort: 'latest' });
  const build = keepsFilter => {
    let clicks = 0;
    return {
      hover: async () => {},
      click: async () => { clicks += 1; },
      evaluate: async script => {
        if (script.startsWith('new Promise')) return;
        const action = script.match(/\},"(entry|panel|option|before|after|rows)",/)[1];
        if (action === 'after') {
          if (clicks === 1) throw new Error('Detached while handling command.');
          return { found: true };
        }
        if (action === 'option') return { found: true, selected: clicks > 0 && keepsFilter };
        return { found: true };
      },
      clicks: () => clicks,
    };
  };
  const kept = build(true);
  await applySearchFilters(kept, options);
  assert.equal(kept.clicks(), 1);
  const lost = build(false);
  await applySearchFilters(lost, options);
  assert.equal(lost.clicks(), 2);
});

// A small DOM fixture executes the actual serialized page function without a browser.
// It checks clone exclusion, exact group/text selection and request + DOM completion.
test('DOM driver chooses the correct hover target and visible option, then waits for refresh', async () => {
  const site = 'xiaohongshu';
  function element(label, attributes = {}, classCount = 1) {
    return {
      innerText: label, children: [], parentElement: null, attributes,
      classList: { length: classCount, contains: name => name === 'active' && classCount === 2 },
      getAttribute: name => attributes[name] ?? null,
      setAttribute: (name, value) => { attributes[name] = value; },
      removeAttribute: name => { delete attributes[name]; },
      closest: () => attributes['aria-hidden'] === 'true' ? {} : null,
      getBoundingClientRect: () => ({ width: 100, height: 20 }),
      querySelectorAll: () => [],
    };
  }
  const entry = element('筛选');
  const parent = element('筛选');
  entry.parentElement = parent;
  const general = element(SORT_LABELS.general, { 'data-index1': '0', 'data-index2': '0' }, 2);
  const clone = element(SORT_LABELS.latest, { 'aria-hidden': 'true', 'data-index1': '0', 'data-index2': '1' });
  const latest = element(SORT_LABELS.latest, { 'data-index1': '0', 'data-index2': '1' });
  const group = element('排序依据');
  group.children = [element('排序依据')];
  group.querySelector = () => latest;
  group.querySelectorAll = () => [general, clone, latest];
  let rows = [element('验证 AI 的结果')];
  let requestCount = 0;
  const nodes = [entry, parent, general, clone, latest];
  const context = vm.createContext({
    document: {
      body: { innerText: '筛选 排序依据 验证 AI 的结果' },
      querySelector: () => null,
      querySelectorAll: selector => {
        if (selector === '*' || selector === 'span') return [entry];
        if (selector === 'div' || selector === '.filter-container .filters') return [group];
        if (selector.startsWith('[data-infoos-')) return nodes.filter(el => el.getAttribute(selector.slice(1, -1)));
        return rows;
      },
    },
    getComputedStyle: () => ({ display: 'block', visibility: 'visible', opacity: '1' }),
    // startTime 5 sits after the mark taken before the click; the stale entry models a cleared-then-refilled buffer.
    performance: { now: () => 1, getEntriesByType: () => [{ name: 'https://www.douyin.com/aweme/v1/web/search/item/?stale', startTime: 0 }, { name: 'https://so.xiaohongshu.com/api/sns/web/v2/search/notes?stale', startTime: 0 }].slice(0, requestCount ? 0 : 2).concat(Array.from({ length: requestCount }, () => ({ startTime: 5, name: site === 'douyin'
      ? 'https://www.douyin.com/aweme/v1/web/search/item/?sort_type=2' : 'https://so.xiaohongshu.com/api/sns/web/v2/search/notes' }))) },
    window: {}, Date,
  });
  const page = {
    evaluate: async script => vm.runInContext(script, context),
    hover: async () => assert.equal((site === 'xiaohongshu' ? parent : entry).getAttribute('data-infoos-filter-entry'), 'true'),
    click: async () => {
      assert.equal(latest.getAttribute('data-infoos-filter-option'), 'true');
      assert.equal(clone.getAttribute('data-infoos-filter-option'), null);
      requestCount++;
      rows = [element('新的结果')];
    },
  };
  await applySearchFilters(page, parseFilters({ sort: 'latest' }));
  assert.equal(requestCount, 1);
});
