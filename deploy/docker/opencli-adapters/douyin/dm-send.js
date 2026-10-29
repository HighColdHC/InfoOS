import { cli, Strategy } from '@jackwener/opencli/registry';
import { messageText, openDmTarget, sendDm } from './_dm.js';

cli({
  site: 'douyin', name: 'dm-send', access: 'write',
  description: '向指定作者或消息列表中的明确联系人发送一条文字私信', domain: 'www.douyin.com',
  strategy: Strategy.UI, navigateBefore: false,
  args: [
    { name: 'recipient', required: true, positional: true, help: '作者 sec_uid/主页 URL，或 contact:<联系人完整名称>' },
    { name: 'text', required: true, positional: true, help: '单行私信正文（最多 500 字符）' },
  ],
  columns: ['kind', 'name', 'author_url', 'status'],
  func: async (page, kwargs) => {
    const text = messageText(kwargs.text);
    const target = await openDmTarget(page, kwargs.recipient);
    return [await sendDm(page, target, text)];
  },
});
