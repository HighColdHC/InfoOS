import { cli, Strategy } from '@jackwener/opencli/registry';
import { ensureZsxqAuth, ensureZsxqPage, fetchFirstJson, getGroupsFromResponse } from './utils.js';

cli({
  site: 'zsxq',
  name: 'whoami',
  access: 'read',
  description: '检查知识星球登录状态',
  domain: 'wx.zsxq.com',
  strategy: Strategy.COOKIE,
  browser: true,
  args: [],
  columns: ['logged_in', 'id', 'name', 'group_count'],
  func: async (page) => {
    await ensureZsxqPage(page);
    await ensureZsxqAuth(page);
    const { data } = await fetchFirstJson(page, ['https://api.zsxq.com/v2/groups']);
    const groups = getGroupsFromResponse(data);
    const owner = groups[0]?.owner || groups[0]?.user || {};
    return [{
      logged_in: true,
      id: String(owner.user_id || owner.uid || groups[0]?.group_id || 'authenticated'),
      name: owner.name || owner.nick_name || '知识星球用户',
      group_count: groups.length,
    }];
  },
});
