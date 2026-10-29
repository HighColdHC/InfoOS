import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import * as session from './feed-session.js';
import { slideDataRow } from './feed-row.js';
import { prefilterOptions, prefilterRow } from './feed-prefilter.js';

function adapter() {
  let command;
  const code = readFileSync(new URL('./feed.js', import.meta.url), 'utf8').replace(/^import .*;$/gm, '');
  const context = vm.createContext({ ...session, slideDataRow, prefilterOptions, prefilterRow, URL,
    cli: value => { command = value; }, Strategy: { COOKIE: 'cookie' },
    ArgumentError: Error, CommandExecutionError: Error, WEB: 'https://www.douyin.com',
    bounded: value => value, evaluate: (page, script) => page.evaluate(script),
  });
  vm.runInContext(code, context);
  return command;
}

function pageFor(states, wheelError) {
  return {
    goto: async () => {}, click: async () => {}, wait: async () => {},
    cdp: async () => { if (wheelError) throw wheelError; },
    evaluate: async script => {
      if (script.includes('slides:')) return { url: 'https://www.douyin.com/?recommend=1', slides: 1 };
      if (!script.includes('const list =')) return false === script.includes('guide-mask');
      const state = states.length > 1 ? states.shift() : states[0];
      if (state instanceof Error) throw state;
      return state;
    },
  };
}

const row = (position, fields = {}) => ({ position, wheel: { x: 1, y: 1 },
  item: { aweme_id: '1234567890123456', ...fields } });
const json = value => JSON.parse(JSON.stringify(value));

test('registered with_meta defaults off and preserves array output; on returns JSON object', async () => {
  const command = adapter();
  assert.equal(command.args.find(arg => arg.name === 'with_meta').default, false);
  const rows = await command.func(pageFor([row(1)]), { limit: 1 });
  assert.deepEqual(json(rows), [{ index: 1, aweme_id: '1234567890123456', feed_url: 'https://www.douyin.com/?recommend=1' }]);
  const result = await command.func(pageFor([row(1)]), { limit: 1, with_meta: true });
  assert.deepEqual(json(result), { items: json(rows), acquisition: {
    requested_count: 1, observed_count: 1, complete: true, stop_reason: null, stop_position: null, stop_message: null,
  } });
});

test('actual advancement paths classify no progress, skipped, unreadable and detached without leaking identity', async () => {
  const cause = Object.assign(new Error('Detached while handling command: account=secret video=1234567890123456'), { code: 'CDP_ERROR' });
  for (const [states, wheelError, reason] of [
    [[row(1)], null, 'no_progress'], [[row(1), row(3)], null, 'skipped_position'],
    [[row(1), { error: 'missing' }], null, 'item_unreadable'],
    [[row(1), cause], null, 'detached'], [[row(1)], cause, 'detached'],
    [[row(1)], new Error('unknown account=secret'), 'unknown'],
  ]) {
    const result = await adapter().func(pageFor(states, wheelError), { limit: 40, with_meta: 'true' });
    assert.equal(result.items.length, 1);
    assert.equal(result.acquisition.complete, false);
    assert.equal(result.acquisition.stop_reason, reason);
    assert.equal(result.acquisition.stop_position, 1);
    assert.ok(result.acquisition.stop_message.length <= 160);
    assert.doesNotMatch(JSON.stringify(result.acquisition), /secret|1234567890123456/);
  }
  const wrapped = session.feedReadError(cause, 'item_unreadable');
  assert.equal(wrapped.code, 'detached');
  assert.equal(wrapped.cause, cause);
  assert.equal(wrapped.cause.code, 'CDP_ERROR');
  assert.equal(session.feedReadError(wrapped), wrapped);
});

test('first read fails; a full batch counts ads and live entries before filtering', async () => {
  await assert.rejects(adapter().func(pageFor([{ error: 'missing' }]), { limit: 40, with_meta: true }),
    error => error.code === 'item_unreadable');
  const result = await adapter().func(pageFor(Array.from({ length: 40 }, (_, i) => row(i + 1, { is_ad: i === 1, is_live: i === 2 }))),
    { limit: 40, with_meta: true });
  assert.equal(result.acquisition.observed_count, 40);
  assert.equal(result.acquisition.complete, true);
});

test('registered feed connects prefilter JSON to page fetch before dwell and keeps observed metadata', async () => {
  const config = { rules_version: 'fixture_v1', token_strengths: { only_fixture_token: 'strong' }, comment_min_count: 20, demand_min_comments: 2,
    tokens: { contact: ['ONLY_FIXTURE_TOKEN'], commercial_intent: [], redirect: [], account_reference: [], comment_demand: [] } };
  const page = pageFor([row(1, { author_url: 'https://www.douyin.com/user/example', comment_count: 0 })]);
  const originalEvaluate = page.evaluate;
  const actions = [];
  page.wait = async ({ time }) => actions.push(['wait', time]);
  page.evaluate = async script => {
    if (!script.includes('async function screenFeedItem')) return originalEvaluate(script);
    return vm.runInNewContext(script, { URL, AbortController, setTimeout, clearTimeout,
      location: { origin: 'https://www.douyin.com' }, fetch: async url => {
        actions.push(['fetch', new URL(url).pathname]);
        return { status: 200, json: async () => ({ status_code: 0, user: { signature: 'only_fixture_token' } }) };
      } });
  };
  const result = await adapter().func(page, { limit: 1, dwell_min: 6, dwell_max: 6,
    with_meta: true, prefilter: JSON.stringify(config) });
  assert.equal(result.items[0].prefilter.status, 'matched');
  assert.equal(result.items[0].author_signature, 'only_fixture_token');
  assert.equal(result.acquisition.complete, true);
  assert.deepEqual(actions.slice(-2), [['fetch', '/aweme/v1/web/user/profile/other/'], ['wait', 6]]);
});
