import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';
import { prefilterOptions, prefilterRow, screenFeedItem } from './feed-prefilter.js';
import { collectFeed } from './feed-session.js';
import { slideDataRow } from './feed-row.js';

// Exercise the actual domain vocabulary, including case variants and symbols.
const config = JSON.parse(execFileSync('python3', ['-B', '-c',
  'import json; from backend.domain.sources.monetization_prefilter import adapter_rules; from backend.domain.sources.douyin_home_feed import home_feed_options; print(json.dumps(adapter_rules(home_feed_options({}))))',
], { cwd: new URL('../../../../', import.meta.url), encoding: 'utf8' }));
const row = { aweme_id: '1234567890123456', author_url: 'https://www.douyin.com/user/example', comment_count: 20 };
const profile = signature => ({ status_code: 0, user: { signature, r_fans_group_info: {} } });

// Run the serialized page function with virtual time, preserving request-end gaps.
async function screenMentions(signature, extras, responses, commentCount = 0, posts = {}) {
  let now = 0;
  const calls = [], waits = [];
  const request = async (path, params) => {
    const call = { path, params, start: now };
    calls.push(call);
    call.params = JSON.parse(JSON.stringify(params));
    now += 275;
    call.end = now;
    if (calls.length === 1) return { ...profile(signature), user: { ...profile(signature).user, signature_extra: extras } };
    const response = path.endsWith('/aweme/post/')
      ? (Object.hasOwn(posts, params.sec_user_id) ? posts[params.sec_user_id] : { status_code: 0, aweme_list: [] })
      : responses[params.sec_user_id] ?? responses.comments;
    if (response instanceof Error) throw response;
    return response;
  };
  const result = await vm.runInNewContext(
    `(${screenFeedItem.toString()})(${JSON.stringify({ ...row, comment_count: commentCount })}, ${JSON.stringify(config)}, request)`,
    { URL, request, setTimeout: (resolve, delay) => { waits.push(delay); now += delay; resolve(); } });
  return { result: JSON.parse(JSON.stringify(result)), calls, waits };
}

test('v3 extracts ordered unique account mentions, excludes self and limits to two even after author match', async () => {
  assert.equal(config.rules_version, 'prefilter_v5');
  const extras = [null, {}, { type: 1, sec_uid: 'hashtag' }, { type: '0', sec_uid: 'string-type' },
    { type: 0, sec_uid: 123 }, { type: 0, sec_uid: '' }, { type: 0, sec_uid: '  ' },
    { type: 0, sec_uid: 'example' }, { type: 0, sec_uid: 'first' }, { type: 0, sec_uid: 'first' },
    { type: 0, sec_uid: 'second' }, { type: 0, sec_uid: 'third' }];
  const { result, calls, waits } = await screenMentions('@朋友', extras, {
    first: { status_code: 0, user: { sec_uid: 'first', signature: 'V VX 商务求教程', r_fans_group_info: { group: 1 } } },
    second: { status_code: 0, user: { sec_uid: 'second', signature: 'V 商务', r_fans_group_info: {} } },
  }, 20);
  assert.deepEqual(calls.map(call => call.params.sec_user_id), ['example', 'first', 'first', 'second', 'second']);
  assert.deepEqual(calls.map(call => call.path), [
    '/aweme/v1/web/user/profile/other/', '/aweme/v1/web/user/profile/other/', '/aweme/v1/web/aweme/post/',
    '/aweme/v1/web/user/profile/other/', '/aweme/v1/web/aweme/post/',
  ]);
  assert.deepEqual(waits, [1000, 1000, 1000, 1000]);
  for (let index = 1; index < calls.length; index++) assert.ok(calls[index].start - calls[index - 1].end >= 1000);
  assert.equal(result.prefilter.status, 'matched');
  assert.equal(result.prefilter.comments_checked, false);
  assert.deepEqual(result.prefilter.hits, [
    { field: 'signature', token: '@', strength: 'weak' }, { field: 'mention_signature', token: 'vx', strength: 'strong' },
    { field: 'mention_signature', token: 'v', strength: 'weak' }, { field: 'mention_signature', token: '商务', strength: 'strong' },
    { field: 'mention_fans_group', token: 'non_empty', strength: 'strong' },
  ]);
  assert.deepEqual(result.prefilter.mentions[0], { sec_uid: 'first', profile_ok: true,
    signature: 'V VX 商务求教程', has_fans_group: true, hits: result.prefilter.hits.slice(1),
    pinned_checked: true, pinned_ok: true, pinned: [] });
  assert.equal(result.prefilter.mentions[1].profile_ok, true);
});

test('mention-only signature or group matches; demand words and non-object groups do not', async () => {
  for (const [signature, group, status] of [['课程', {}, 'matched'], ['', { group: 1 }, 'matched'],
    ['求教程', {}, 'missed'], ['', [], 'missed'], ['', 'group', 'missed']]) {
    const { result } = await screenMentions('日常', [{ type: 0, sec_uid: 'target' }], {
      target: { status_code: 0, user: { sec_uid: 'target', signature, r_fans_group_info: group } },
    });
    assert.equal(result.prefilter.status, status);
    assert.equal(result.prefilter.mentions[0].signature, signature);
    assert.equal(result.prefilter.mentions[0].profile_ok, true);
  }
  for (const extras of [undefined, null, {}, []]) {
    const { result, calls } = await screenMentions('日常', extras, {});
    assert.deepEqual(result.prefilter.mentions, []);
    assert.equal(calls.length, 1);
  }
});

test('mention failure preserves matched/missed and existing comment verdict; next target still runs', async () => {
  const badProfiles = [new Error('timeout'), null, {}, { status_code: 1 },
    { status_code: 0, user: { sec_uid: 'wrong', signature: '商务' } },
    { status_code: 0, user: { signature: '商务' } }, { status_code: 0, user: { sec_uid: 'bad' } }];
  for (const bad of badProfiles) {
    for (const [signature, expected] of [['商务@朋友', 'matched'], ['@朋友', 'missed'], ['日常', 'missed']]) {
      const { result, calls } = await screenMentions(signature,
        [{ type: 0, sec_uid: 'bad' }, { type: 0, sec_uid: 'good' }], {
          bad, good: { status_code: 0, user: { sec_uid: 'good', signature: '' } },
        });
      assert.equal(result.prefilter.status, expected);
      assert.equal(calls.length, 4);
      assert.deepEqual(result.prefilter.mentions[0], {
        sec_uid: 'bad', profile_ok: false, signature: '', hits: [], has_fans_group: false,
        pinned_checked: false, pinned_ok: null, pinned: [],
      });
      assert.equal(result.prefilter.mentions[1].profile_ok, true);
      assert.ok(calls[2].start - calls[1].end >= 1000);
    }
  }
  for (const [comments, expected] of [[[], 'missed'], [[{ text: '求教程' }, { text: '蹲' }], 'matched']]) {
    const { result, calls } = await screenMentions('日常', [{ type: 0, sec_uid: 'bad' }], {
      bad: new Error('timeout'), comments: { status_code: 0, comments },
    }, 20);
    assert.equal(result.prefilter.status, expected);
    assert.equal(result.prefilter.comments_ok, true);
    assert.equal(calls.length, 3);
  }
});

test('profile token categories match literally and ASCII case-insensitively; demand words only apply to comments', async () => {
  const { comment_demand: demand, ...profileTokens } = config.tokens;
  for (const tokens of Object.values(profileTokens)) {
    for (const token of tokens) {
      const result = await screenFeedItem({ ...row, comment_count: 0 }, config, async () => profile(`前${token.toUpperCase()}后`));
      assert.equal(result.prefilter.status, config.token_strengths[token.toLowerCase()] === 'strong' ? 'matched' : 'missed', token);
      assert.ok(result.prefilter.hits.some(hit => hit.field === 'signature' && hit.token === token.toLowerCase()), token);
    }
  }
  const bothCases = await screenFeedItem({ ...row, comment_count: 0 }, { ...config, tokens: { ...config.tokens, contact: ['v', 'V'] } },
    async () => profile('Vlog'));
  assert.deepEqual(bothCases.prefilter.hits, [{ field: 'signature', token: 'v', strength: 'weak' }]);
  for (const token of demand) {
    const result = await screenFeedItem({ ...row, comment_count: 0 }, config, async () => profile(`前${token}后`));
    assert.ok(!result.prefilter.hits.some(hit => hit.token === token), token);
  }
  assert.equal(prefilterOptions(), null);
  assert.deepEqual(prefilterOptions(JSON.stringify(config)), config);
  assert.throws(() => prefilterOptions(JSON.stringify({ ...config, demand_min_comments: 21 })));
});

test('profile match stops after one request; shops, permissions, verification and video flags do not match', async () => {
  const signals = { ...profile('记录生活').user, with_fusion_shop_entry: true, with_commerce_entry: true,
    commerce_user_info: { has_ads_entry: true }, can_show_group_card: 1, custom_verify: '商务认证',
    enterprise_user_info: JSON.stringify({ permissions: [{ Key: 'UserShop', Name: '个人橱窗', Status: 1 }] }) };
  let calls = 0;
  const result = await screenFeedItem({ ...row, comment_count: 19, prefilter_signals: { text_extra_is_commerce: [true] } }, config,
    async () => { calls++; return { status_code: 0, user: signals }; });
  assert.equal(calls, 1);
  assert.equal(result.prefilter.status, 'missed');
  assert.equal(result.author_signature, '记录生活');
  assert.equal(result.prefilter.signals.enterprise_permissions[0].Key, 'UserShop');
  assert.equal(result.prefilter.comments_checked, false);
  assert.equal(result.prefilter.comments_ok, null);
  for (const user of [{ ...signals, signature: '商务合作' }, { ...signals, r_fans_group_info: { group: 1 } }]) {
    let requests = 0;
    const matched = await screenFeedItem(row, config, async () => { requests++; return { status_code: 0, user }; });
    assert.equal(matched.prefilter.status, 'matched');
    assert.equal(requests, 1);
    assert.ok(matched.prefilter.hits.length);
  }
});

test('comments use one first-page request; count matching comments, exclude replies, output no identities/text', async () => {
  for (const [texts, expected, count] of [
    [['求教程求链接', '其他'], 'missed', 1], [['求教程', '蹲'], 'matched', 2], [['其他'], 'missed', 0],
  ]) {
    const calls = [];
    const result = await screenFeedItem(row, config, async (path, params) => {
      calls.push([path, params]);
      return calls.length === 1 ? profile('日常') : { status_code: 0, comments: texts.map(text => ({
        text, cid: 'private-comment-id', user: { nickname: 'private-nickname' }, reply_comment: [{ text: '求教程' }],
      })) };
    });
    assert.equal(result.prefilter.status, expected);
    assert.equal(result.prefilter.demand_comment_count, count);
    assert.equal(result.prefilter.comments_ok, true);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[1], ['/aweme/v1/web/comment/list/', { aweme_id: row.aweme_id, count: 20, cursor: 0 }]);
    assert.doesNotMatch(JSON.stringify(result), /private-|reply_comment|求教程求链接/);
  }
});

test('profile/comment errors and anomalous payloads are unknown without retry', async () => {
  for (const bad of [null, {}, { status_code: 1 }, { status_code: 0, user: {} },
    { status_code: 0, user: { signature: '生活', enterprise_user_info: '{bad' } }, new Error('timeout')]) {
    let count = 0;
    const result = await screenFeedItem(row, config, async () => { count++; if (bad instanceof Error) throw bad; return bad; });
    assert.equal(result.prefilter.status, 'unknown');
    assert.equal(count, 1);
  }
  for (const bad of [null, {}, { status_code: 1, comments: [] }, { status_code: 0, comments: [{}] }, new Error('timeout')]) {
    let count = 0;
    const result = await screenFeedItem(row, config, async () => {
      if (++count === 1) return profile('日常');
      if (bad instanceof Error) throw bad;
      return bad;
    });
    assert.equal(count, 2);
    assert.equal(result.prefilter.status, 'unknown');
    assert.equal(result.prefilter.profile_ok, true);
    assert.equal(result.prefilter.comments_checked, true);
    assert.equal(result.prefilter.comments_ok, false);
  }
  const failed = await prefilterRow({}, row, config, async () => { throw new Error('detached'); });
  assert.equal(failed.prefilter.status, 'unknown');
});

test('page fetch uses credentials, no navigation, 5000ms abort deadline including response parsing', async () => {
  let calls = 0, timerCallback, signal;
  const context = { URL, AbortController, location: { origin: 'https://www.douyin.com' },
    setTimeout: (callback, delay) => { assert.equal(delay, 5000); timerCallback = callback; return 1; },
    clearTimeout: () => {},
    fetch: async (url, options) => {
      calls++; signal = options.signal;
      assert.equal(typeof url, 'string');
      url = new URL(url);
      assert.equal(options.method, 'GET'); assert.equal(options.credentials, 'include');
      assert.ok(['/aweme/v1/web/user/profile/other/', '/aweme/v1/web/aweme/post/'].includes(url.pathname));
      assert.equal(url.searchParams.get('aid'), '6383');
      assert.equal(url.searchParams.get('sec_user_id'), 'example');
      return { status: 200, json: () => new Promise(() => {}) };
    } };
  const promise = vm.runInNewContext(`(${screenFeedItem.toString()})(${JSON.stringify(row)}, ${JSON.stringify(config)})`, context);
  timerCallback();
  assert.equal((await promise).prefilter.status, 'unknown');
  assert.equal(calls, 1); assert.equal(signal.aborted, true);
});

test('mention page GET shares the 5s JSON deadline and continues after timeout', async () => {
  const calls = [], delays = [];
  let deadline;
  const context = { URL, AbortController, location: { origin: 'https://www.douyin.com' },
    setTimeout: (callback, delay) => {
      delays.push(delay);
      if (delay === 1000) callback();
      else { assert.equal(delay, 5000); deadline = callback; }
      return 1;
    },
    clearTimeout: () => {},
    fetch: async (url, options) => {
      assert.equal(typeof url, 'string');
      url = new URL(url);
      const target = url.searchParams.get('sec_user_id');
      calls.push(target);
      assert.ok(['/aweme/v1/web/user/profile/other/', '/aweme/v1/web/aweme/post/'].includes(url.pathname));
      assert.equal(url.searchParams.get('aid'), '6383');
      assert.equal(options.method, 'GET');
      assert.equal(options.credentials, 'include');
      return { status: 200, json: async () => {
        if (url.pathname.endsWith('/aweme/post/')) return { status_code: 0, aweme_list: [] };
        if (target === 'example') return { status_code: 0, user: { signature: '@朋友',
          signature_extra: [{ type: 0, sec_uid: 'timeout' }, { type: 0, sec_uid: 'next' }] } };
        if (target === 'timeout') {
          deadline();
          assert.equal(options.signal.aborted, true);
          return new Promise(() => {});
        }
        return { status_code: 0, user: { sec_uid: 'next', signature: '课程' } };
      } };
    } };
  const result = await vm.runInNewContext(`(${screenFeedItem.toString()})(${JSON.stringify(row)}, ${JSON.stringify(config)})`, context);
  assert.deepEqual(calls, ['example', 'timeout', 'next', 'next']);
  assert.deepEqual(delays, [5000, 1000, 5000, 1000, 5000, 1000, 5000]);
  assert.equal(result.prefilter.status, 'matched');
  assert.equal(result.prefilter.mentions[0].profile_ok, false);
  assert.equal(result.prefilter.mentions[1].profile_ok, true);
  assert.ok(result.prefilter.hits.some(hit => hit.field === 'mention_signature' && hit.token === '课程'));
});

test('ads/live skip all profile/comment requests; enabled signals are absent when off', async () => {
  for (const field of ['is_ad', 'is_live']) {
    const result = await screenFeedItem({ ...row, [field]: true }, config, () => assert.fail('request'));
    assert.equal(result.prefilter.status, 'skipped');
  }
  assert.equal('prefilter_signals' in slideDataRow({}), false);
  assert.deepEqual(slideDataRow({ textExtra: [{ isCommerce: true }] }, true).prefilter_signals.text_extra_is_commerce, [true]);
});

test('missed dwell 1-2s, matched/unknown dwell normally, all observations remain complete', async () => {
  const statuses = ['missed', 'matched', 'unknown', 'missed'];
  for (const random of [() => 0, () => 0.999]) {
    const waits = [];
    const state = position => ({ position, item: { ...row, position } });
    const result = await collectFeed({ limit: 4, read: async () => state(1), advance: async prev => state(prev.position + 1),
      wait: async value => waits.push(value), dwellMin: 6, dwellMax: 20, random, withMeta: true,
      prefilter: async item => ({ ...item, prefilter: { status: statuses[item.position - 1] } }) });
    assert.deepEqual(waits, random() === 0 ? [1, 6, 6, 1] : [2, 20, 20, 2]);
    assert.equal(result.items.length, 4); assert.equal(result.acquisition.observed_count, 4);
    assert.equal(result.acquisition.complete, true);
  }
});

test('pinned first page keeps only three native pins and bounded descriptions; merges folded hits', async () => {
  const extras = [{ type: 0, sec_uid: 'first' }, { type: 0, sec_uid: 'second' }];
  const profiles = Object.fromEntries(extras.map(({ sec_uid }) =>
    [sec_uid, { status_code: 0, user: { sec_uid, signature: '' } }]));
  const posts = { status_code: 0, has_more: 1, max_cursor: 'private-cursor', aweme_list: [
    { is_top: 0, desc: 'private-unpinned 商务' }, { is_top: '1', desc: 'private-string-pin 合作' },
    { is_top: 1, desc: 'V VX v 课程求教程', aweme_id: 'private-id', video: { url: 'private-media' } },
    { is_top: true, desc: '课'.repeat(299) + '程商务' },
    { is_top: 1, desc: '😀'.repeat(301) }, { is_top: true, desc: 'private-fourth 合作' },
  ] };
  const { result, calls, waits } = await screenMentions('日常', extras, profiles, 20, { first: posts, second: posts });
  assert.equal(result.prefilter.status, 'matched');
  assert.equal(result.prefilter.comments_checked, false);
  assert.equal(calls.length, 5);
  assert.deepEqual(waits, [1000, 1000, 1000, 1000]);
  for (let i = 1; i < calls.length; i++) assert.ok(calls[i].start - calls[i - 1].end >= 1000);
  assert.deepEqual(calls.filter(call => call.path.endsWith('/aweme/post/')).map(call => call.params),
    extras.map(({ sec_uid }) => ({ sec_user_id: sec_uid, max_cursor: 0, count: 20 })));
  const expectedHits = [{ field: 'mention_pinned', token: 'vx', strength: 'strong' }, { field: 'mention_pinned', token: 'v', strength: 'weak' },
    { field: 'mention_pinned', token: '课程', strength: 'strong' }];
  assert.deepEqual(result.prefilter.hits, expectedHits);
  for (const mention of result.prefilter.mentions) {
    assert.equal(mention.pinned_checked, true);
    assert.equal(mention.pinned_ok, true);
    assert.deepEqual(mention.hits, expectedHits);
    assert.deepEqual(mention.pinned, [
      { desc: 'V VX v 课程求教程', hits: expectedHits },
      { desc: '课'.repeat(299) + '程', hits: [{ field: 'mention_pinned', token: '课程', strength: 'strong' }] },
      { desc: '😀'.repeat(300), hits: [] },
    ]);
  }
  assert.doesNotMatch(JSON.stringify(result), /aweme_list|is_top|private-|has_more|max_cursor/);
  const demandOnly = await screenMentions('日常', [extras[0]], profiles, 0,
    { first: { status_code: 0, aweme_list: [{ is_top: 1, desc: config.tokens.comment_demand.join(' ') }] } });
  assert.equal(demandOnly.result.prefilter.status, 'missed');
  assert.deepEqual(demandOnly.result.prefilter.mentions[0].pinned[0].hits, []);
});

test('pinned failures preserve author, mention and comment verdicts and continue to next target', async () => {
  for (const bad of [new Error('timeout'), null, {}, { status_code: 1, aweme_list: [] },
    { status_code: 0, aweme_list: {} }, { status_code: 0, aweme_list: [{ is_top: 1 }] }]) {
    for (const [signature, mentionSignature, comments, expected] of [
      ['商务', '', [], 'matched'], ['日常', '课程', [], 'matched'], ['日常', '', [], 'missed'],
      ['日常', '', [{ text: '求教程' }, { text: '蹲' }], 'matched'], ['日常', '', null, 'unknown'],
    ]) {
      const { result, calls } = await screenMentions(signature,
        [{ type: 0, sec_uid: 'first' }, { type: 0, sec_uid: 'second' }], {
          first: { status_code: 0, user: { sec_uid: 'first', signature: mentionSignature } },
          second: { status_code: 0, user: { sec_uid: 'second', signature: '' } },
          comments: { status_code: 0, comments },
        }, 20, { first: bad });
      assert.equal(result.prefilter.status, expected);
      assert.equal(result.prefilter.mentions[0].pinned_checked, true);
      assert.equal(result.prefilter.mentions[0].pinned_ok, false);
      assert.deepEqual(result.prefilter.mentions[0].pinned, []);
      assert.equal(result.prefilter.mentions[1].pinned_ok, true);
      assert.ok(calls[3].start - calls[2].end >= 1000);
    }
  }
});

test('pinned real fetch path uses string URL and 5s deadline for fetch and JSON, without retry', async () => {
  for (const failure of ['fetch-timeout', 'json-timeout', 'http', 'json', 'success']) {
    const calls = [], delays = [];
    let deadline, pinnedSignal;
    const context = { URL, AbortController, location: { origin: 'https://www.douyin.com' },
      setTimeout: (callback, delay) => {
        delays.push(delay);
        if (delay === 1000) callback();
        else deadline = callback;
        return 1;
      }, clearTimeout: () => {},
      fetch: async (input, options) => {
        const url = new URL(input);
        calls.push({ input, url, options });
        const target = url.searchParams.get('sec_user_id');
        if (url.pathname.endsWith('/aweme/post/')) {
          pinnedSignal = options.signal;
          if (failure === 'fetch-timeout') {
            Promise.resolve().then(() => deadline());
            return new Promise(() => {});
          }
          return { status: failure === 'http' ? 403 : 200, json: async () => {
            if (failure === 'json-timeout') { deadline(); return new Promise(() => {}); }
            if (failure === 'json') throw new SyntaxError('invalid JSON');
            return { status_code: 0, aweme_list: [{ is_top: true, desc: '课程' }] };
          } };
        }
        return { status: 200, json: async () => ({ status_code: 0, user: {
          sec_uid: target, signature: '日常',
          signature_extra: target === 'example' ? [{ type: 0, sec_uid: 'target' }] : [],
        } }) };
      } };
    const result = await vm.runInNewContext(
      `(${screenFeedItem.toString()})(${JSON.stringify({ ...row, comment_count: 0 })}, ${JSON.stringify(config)})`, context);
    assert.equal(calls.length, 3);
    assert.deepEqual(delays, [5000, 1000, 5000, 1000, 5000]);
    const post = calls[2];
    assert.equal(typeof post.input, 'string');
    assert.equal(post.url.origin, 'https://www.douyin.com');
    assert.equal(post.url.pathname, '/aweme/v1/web/aweme/post/');
    assert.deepEqual(Object.fromEntries(post.url.searchParams),
      { aid: '6383', sec_user_id: 'target', max_cursor: '0', count: '20' });
    assert.equal(post.options.method, 'GET');
    assert.equal(post.options.credentials, 'include');
    assert.equal(pinnedSignal.aborted, failure.endsWith('timeout'));
    assert.equal(result.prefilter.mentions[0].pinned_ok, failure === 'success');
    assert.equal(result.prefilter.status, failure === 'success' ? 'matched' : 'missed');
  }
});


test('weak author and mention/pinned evidence never qualifies alone; strong secondary evidence does', async () => {
  for (const text of ['@', '：', '@ ：', 'v', 'V', '🌍', '🌏', '👇', '⬇️', '戳']) {
    for (const field of ['signature', 'pinned']) {
      for (const suffix of ['', ' 私信']) {
        const { result } = await screenMentions('@ ：', [{ type: 0, sec_uid: 'target' }], {
          target: { status_code: 0, user: { sec_uid: 'target', signature: field === 'signature' ? text + suffix : '' } },
        }, 0, { target: { status_code: 0, aweme_list: field === 'pinned' ? [{ is_top: 1, desc: text + suffix }] : [] } });
        assert.equal(result.prefilter.status, suffix ? 'matched' : 'missed');
        const hits = result.prefilter.hits;
        assert.ok(hits.some(hit => hit.strength === 'weak'));
        assert.equal(hits.some(hit => hit.strength === 'strong'), Boolean(suffix));
      }
    }
  }
});

test('weak author hits survive comment threshold outcomes and unknown failures', async () => {
  for (const [comments, status] of [[[], 'missed'], [[{ text: '求教程' }], 'missed'],
    [[{ text: '求教程' }, { text: '蹲' }], 'matched'], [null, 'unknown']]) {
    const { result } = await screenMentions('@ ：', [], { comments: { status_code: 0, comments } }, 20);
    assert.equal(result.prefilter.status, status);
    assert.ok(result.prefilter.hits.some(hit => hit.token === '@' && hit.strength === 'weak'));
    assert.ok(result.prefilter.hits.some(hit => hit.token === '：' && hit.strength === 'weak'));
    assert.ok(result.prefilter.hits.filter(hit => hit.field === 'comments.text').every(hit => hit.strength === 'strong'));
  }
});
