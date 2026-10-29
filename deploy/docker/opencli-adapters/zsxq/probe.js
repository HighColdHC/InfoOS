import { cli, Strategy } from '@jackwener/opencli/registry';
import { AuthRequiredError, CliError } from '@jackwener/opencli/errors';
import { createZsxqRequestControl } from './request-control.js';
import { browserJsonRequest, ensureZsxqPage, DEFAULT_GROUP_ID, unwrapRespData } from './utils.js';

export async function probeGroup(page, groupId = DEFAULT_GROUP_ID) {
  await ensureZsxqPage(page);
  const control = createZsxqRequestControl({ env: { ...process.env, INFOOS_ZSXQ_1059_RETRIES: '0' } });
  const result = await browserJsonRequest(page,
    `https://api.zsxq.com/v2/groups/${groupId}/topics?scope=all&count=1`, control);
  // Match OpenCLI's existing authentication error path; retain network/timeout errors.
  if (result.status === 401 || Number(result.data?.code) === 401
      || (result.data?.succeeded === false && /未登录|请.*登录|not logged|auth.required|login required/i.test(result.data?.info || ''))) {
    throw new AuthRequiredError('zsxq.com');
  }
  if (!result.ok) throw new CliError('FETCH_ERROR', result.error || 'ZSXQ probe failed');
  unwrapRespData(result.data);
  return [{ status: 'ok' }];
}

cli({
  site: 'zsxq', name: 'probe', access: 'read', description: '单请求检查知识星球风控是否恢复',
  domain: 'wx.zsxq.com', strategy: Strategy.COOKIE, browser: true,
  args: [{ name: 'group_id', help: 'Optional group id' }],
  columns: ['status'],
  func: (page, kwargs) => probeGroup(page, String(kwargs.group_id || DEFAULT_GROUP_ID)),
});
