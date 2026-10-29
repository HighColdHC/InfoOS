import { cli, Strategy } from '@jackwener/opencli/registry';
import { groups, openMessages } from './_groups.js';

cli({
  site: 'douyin', name: 'group-list', access: 'read',
  description: '读取网页消息面板中可识别的已加入群聊及最近消息预览', domain: 'www.douyin.com',
  strategy: Strategy.UI, navigateBefore: false,
  args: [], columns: ['group_name', 'last_message_preview', 'last_message_time'],
  func: async (page) => {
    await openMessages(page);
    return (await groups(page)).map((row) => ({
      group_name: row.name, last_message_preview: row.preview, last_message_time: row.time,
    }));
  },
});
