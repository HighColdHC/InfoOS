import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { boundedInt, imGet, numericId, openPage, XHS_WEB } from './_shared.js';

export function readableMessage(message, groupId) {
  let content = {};
  try { content = JSON.parse(String(message.content || '{}')); } catch { content = {}; }
  const text = message.revoked ? '' : typeof content.content === 'string' && content.content.trim()
    ? content.content.trim()
    : (typeof content.front_chain === 'string' ? content.front_chain.trim() : '');
  return {
    group_id: groupId,
    message_id: String(message.id || message.uuid || ''),
    store_id: Number(message.store_id || 0),
    sender_id: String(message.sender_id || ''),
    created_at: Number(message.created_at || 0),
    content_type: Number(content.content_type ?? -1),
    text,
    revoked: Boolean(message.revoked),
  };
}

cli({
  site: 'xiaohongshu', name: 'group-messages', access: 'read',
  description: '读取当前账号已加入群的最近消息；last-id 可向更早的 store ID 翻页',
  domain: 'www.xiaohongshu.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [
    { name: 'group-id', required: true, positional: true, help: 'group-list 返回的群 ID' },
    { name: 'limit', type: 'int', default: 20, help: '本页消息条数（1-50）' },
    { name: 'last-id', type: 'string', default: '0', help: '包含此 store ID 的历史页；0 为最新页' },
  ],
  columns: ['group_id', 'message_id', 'store_id', 'sender_id', 'created_at', 'content_type', 'text', 'revoked'],
  func: async (page, kwargs) => {
    const groupId = numericId(kwargs['group-id'], 'group-id');
    const lastId = numericId(kwargs['last-id'] ?? '0', 'last-id');
    const limit = boundedInt(kwargs.limit, 'limit');
    await openPage(page, `${XHS_WEB}/chat`, '/chat');
    const data = await imGet(page, '/api/im/web/red/group/messages/history', {
      group_id: groupId, last_id: lastId, start_id: 0, limit,
    });
    if (!data || !Array.isArray(data.out_message_list)) {
      throw new CommandExecutionError('Xiaohongshu returned no group message list');
    }
    return data.out_message_list.slice(0, limit).map((message) => readableMessage(message, groupId));
  },
});
