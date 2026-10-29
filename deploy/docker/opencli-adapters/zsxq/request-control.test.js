import assert from 'node:assert/strict';
import test from 'node:test';
import { createZsxqRequestControl } from './request-control.js';

const url = 'https://api.zsxq.com/v2/topics/123';
const limited = () => ({ data: { succeeded: false, code: 1059 } });
function clock(env = {}, random = () => 0.5) {
  let now = 0;
  const sleeps = [];
  return { sleeps, now: () => now, control: createZsxqRequestControl({ env, random,
    now: () => now, sleep: async ms => { sleeps.push(ms); now += ms; } }) };
}

test('serializes concurrent API calls with minimum interval and jitter across pages', async () => {
  const { control, now, sleeps } = clock();
  const starts = [];
  await Promise.all([1, 2, 3].map(() => control.run(url, async () => {
    starts.push(now());
    return { ok: true };
  })));
  assert.deepEqual(starts, [0, 2000, 4000]);
  assert.deepEqual(sleeps, [2000, 2000]);
});

test('environment overrides pacing; Feishu requests do not consume its budget', async () => {
  const { control, sleeps } = clock({ INFOOS_ZSXQ_REQUEST_INTERVAL_MS: '50', INFOOS_ZSXQ_REQUEST_JITTER_MS: '20' });
  for (const path of [url, 'https://tenant.feishu.cn/docx/abc', url]) {
    await control.run(path, async () => ({ ok: true }));
  }
  assert.deepEqual(sleeps, [60]);
});

test('1059 defaults to one 20s retry then throws RATE_LIMITED', async () => {
  const { control, sleeps } = clock();
  let requests = 0;
  await assert.rejects(control.run(url, async () => { requests += 1; return limited(); }), error => {
    assert.equal(error.code, 'RATE_LIMITED');
    assert.equal(error.message, 'zsxq risk control 1059 (rate limited)');
    return true;
  });
  assert.equal(requests, 2);
  assert.deepEqual(sleeps, [20000]);
});

test('caps retries at two and total command cooling at 60s across multiple API calls', async () => {
  const { control, sleeps } = clock({ INFOOS_ZSXQ_1059_RETRIES: '9' });
  let requests = 0;
  await assert.rejects(control.run(url, async () => { requests += 1; return limited(); }), { code: 'RATE_LIMITED' });
  assert.equal(requests, 3);
  await assert.rejects(control.run(url, async () => { requests += 1; return limited(); }), { code: 'RATE_LIMITED' });
  assert.equal(requests, 5);
  assert.equal(sleeps.filter(ms => ms === 20000).reduce((a, b) => a + b, 0), 60000);
});

test('one transient 1059 recovers and other API errors are not retried', async () => {
  const { control, sleeps } = clock();
  const responses = [limited(), { data: { succeeded: true } }];
  assert.equal((await control.run(url, async () => responses.shift())).data.succeeded, true);
  assert.deepEqual(sleeps, [20000]);
  const error = { data: { succeeded: false, code: 401 } };
  assert.equal(await control.run(url, async () => error), error);
});

test('slow signing or bridge work cannot compress the next request interval', async () => {
  let now = 0;
  const starts = [];
  const control = createZsxqRequestControl({ env: {}, now: () => now, random: () => 1,
    sleep: async ms => { now += ms; } });
  await control.run(url, async () => { now += 3000; starts.push(now); return {}; });
  await control.run(url, async () => { starts.push(now); return {}; });
  assert.deepEqual(starts, [3000, 5500]);
});
