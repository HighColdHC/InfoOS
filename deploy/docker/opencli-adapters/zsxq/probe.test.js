import assert from 'node:assert/strict';
import test from 'node:test';
import { getRegistry } from '@jackwener/opencli/registry';
import './probe.js';

function fakePage(response) {
  const urls = [];
  return { urls, goto: async () => {}, evaluate: async script => {
    if (!script.includes('const path =')) return 'same-device';
    urls.push(JSON.parse(script.match(/const path = (.*);/)[1]));
    return response;
  } };
}
for (const [name, response, code] of [
  ['success', { ok: true, data: { succeeded: true, resp_data: { topics: [] } } }, null],
  ['1059', { ok: true, data: { succeeded: false, code: 1059 } }, 'RATE_LIMITED'],
  ['not logged in', { ok: false, status: 401 }, 'AUTH_REQUIRED'],
  ['API login failure', { ok: true, data: { succeeded: false, code: 401 } }, 'AUTH_REQUIRED'],
  ['timeout', { ok: false, error: 'ZSXQ request timeout after 15s' }, 'FETCH_ERROR'],
]) {
  test(`probe ${name} makes exactly one API request, without auth/group lookup or 1059 retry`, async () => {
    const page = fakePage(response);
    const call = () => getRegistry().get('zsxq/probe').func(page, { group_id: '42' });
    if (code) await assert.rejects(call(), error => error.code === code);
    else assert.deepEqual(await call(), [{ status: 'ok' }]);
    assert.deepEqual(page.urls, ['https://api.zsxq.com/v2/groups/42/topics?scope=all&count=1']);
  });
}
