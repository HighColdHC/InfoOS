import { cli, Strategy } from '@jackwener/opencli/registry';
import { contacts } from './_dm.js';

cli({
  site: 'douyin', name: 'dm-contacts', access: 'read',
  description: '读取网页消息列表中可识别的一对一联系人', domain: 'www.douyin.com',
  strategy: Strategy.UI, navigateBefore: false,
  args: [], columns: ['name', 'last_message_preview', 'last_message_time', 'recipient'],
  func: async (page) => (await contacts(page)).map(row => ({
    name: row.name, last_message_preview: row.preview, last_message_time: row.time,
    recipient: `contact:${row.name}`,
  })),
});
