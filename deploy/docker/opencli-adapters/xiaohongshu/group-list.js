import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { boundedInt, imGet, openPage, XHS_WEB } from './_shared.js';

cli({
  site: 'xiaohongshu', name: 'group-list', access: 'read',
  description: '列出当前账号已经加入的小红书群聊',
  domain: 'www.xiaohongshu.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [
    { name: 'page', type: 'int', default: 0, help: '从 0 开始的页码' },
    { name: 'limit', type: 'int', default: 20, help: '每页群数（1-50）' },
  ],
  columns: ['group_id', 'name', 'member_count', 'last_message_time', 'last_store_id', 'page'],
  func: async (page, kwargs) => {
    const pageNumber = boundedInt(kwargs.page, 'page', { min: 0, max: 1000, fallback: 0 });
    const limit = boundedInt(kwargs.limit, 'limit');
    await openPage(page, `${XHS_WEB}/chat`, '/chat');
    const data = await imGet(page, '/api/im/web/chats/group', {
      limit, complete: 'true', page: pageNumber, source: 'pc',
    });
    if (!data || !Array.isArray(data.chats)) {
      throw new CommandExecutionError('Xiaohongshu returned no group list');
    }
    return data.chats.slice(0, limit).filter((group) => group.group_id).map((group) => ({
      group_id: String(group.group_id),
      name: String(group.info?.group_name || ''),
      member_count: Number(group.info?.user_num || 0),
      last_message_time: Number(group.last_msg_time || 0),
      last_store_id: Number(group.max_store_id || 0),
      page: pageNumber,
    }));
  },
});
