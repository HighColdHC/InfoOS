import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { groupName, groups, openMessages } from './_groups.js';

cli({
  site: 'douyin', name: 'group-messages', access: 'read',
  description: '读取已加入群在网页消息列表里的最新消息预览', domain: 'www.douyin.com',
  strategy: Strategy.UI, navigateBefore: false,
  args: [{ name: 'group-name', required: true, positional: true, help: 'group-list 返回的完整群名' }],
  columns: ['group_name', 'text', 'time', 'scope'],
  func: async (page, kwargs) => {
    const name = groupName(kwargs['group-name']);
    await openMessages(page);
    const matches = (await groups(page)).filter((row) => row.name === name);
    if (matches.length !== 1) throw new CommandExecutionError('Group name is absent or ambiguous in Douyin message list');
    if (!matches[0].preview) throw new CommandExecutionError('This group has no visible message preview');
    return [{ group_name: name, text: matches[0].preview, time: matches[0].time, scope: 'latest_preview_only' }];
  },
});
