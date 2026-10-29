import { cli, Strategy } from '@jackwener/opencli/registry';
import { openDmTarget } from './_dm.js';

cli({
  site: 'douyin', name: 'dm-target', access: 'read',
  description: '只打开并核对抖音作者或联系人的一对一私信会话，不发送消息', domain: 'www.douyin.com',
  strategy: Strategy.UI, navigateBefore: false,
  args: [{ name: 'recipient', required: true, positional: true, help: '作者 sec_uid/主页 URL，或 contact:<联系人完整名称>' }],
  columns: ['kind', 'name', 'author_url', 'status'],
  func: async (page, kwargs) => [{ ...await openDmTarget(page, kwargs.recipient), status: 'ready' }],
});
