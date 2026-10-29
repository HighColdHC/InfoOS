import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';
import { evaluate, imGet, openPage, XHS_WEB } from './_shared.js';

function inviteUrl(value) {
  let parsed;
  try { parsed = new URL(String(value ?? '').trim()); } catch { throw new ArgumentError('Expected a full Xiaohongshu group or author URL'); }
  if (parsed.protocol !== 'https:' || !['www.xiaohongshu.com', 'xhslink.com'].includes(parsed.hostname)) {
    throw new ArgumentError('Group URL must use www.xiaohongshu.com or xhslink.com');
  }
  return parsed.toString();
}

async function joinedGroups(page) {
  await openPage(page, `${XHS_WEB}/chat`, '/chat');
  const data = await imGet(page, '/api/im/web/chats/group', {
    limit: 100, complete: 'true', page: 0, source: 'pc',
  });
  if (!Array.isArray(data?.chats)) throw new CommandExecutionError('Cannot verify Xiaohongshu group membership');
  return data.chats.filter((row) => row.group_id).map((row) => ({
    groupId: String(row.group_id), name: String(row.info?.group_name || ''),
  }));
}

cli({
  site: 'xiaohongshu', name: 'group-join', access: 'write',
  description: '从一个明确的网页群邀请或作者主页加入群聊，并读回成员状态',
  domain: 'www.xiaohongshu.com', strategy: Strategy.UI, navigateBefore: false,
  args: [{ name: 'group-url', required: true, positional: true, help: '网页群邀请或展示群入口的作者主页 URL' }],
  columns: ['status', 'group_id', 'group_name', 'url'],
  func: async (page, kwargs) => {
    const targetUrl = inviteUrl(kwargs['group-url']);
    const before = await joinedGroups(page);
    await page.goto(targetUrl);
    await page.wait({ time: 1 });
    const current = await evaluate(page, '() => location.href', 'group invite navigation');
    const resolved = new URL(current);
    if (resolved.hostname !== 'www.xiaohongshu.com') {
      throw new CommandExecutionError('Group invite did not resolve to the Xiaohongshu web page');
    }
    if (resolved.pathname.startsWith('/login')) throw new AuthRequiredError('www.xiaohongshu.com');
    const action = await evaluate(page, `(() => {
      const labels = new Set(['加入群聊', '加入该群', '申请加入', '申请入群']);
      const candidates = [...document.querySelectorAll('button, [role="button"], a')]
        .filter((el) => el.getClientRects().length && labels.has((el.textContent || '').trim()));
      if (candidates.length !== 1) return { found: false, count: candidates.length };
      candidates[0].click();
      return { found: true };
    })()`, 'group join control');
    if (!action.found) {
      throw new CommandExecutionError(`Expected one visible group join control, found ${action.count ?? 0}; no join attempted`);
    }
    await page.wait({ time: 2 });
    const pending = await evaluate(page, `(() => [...document.querySelectorAll('button, [role="button"]')]
      .some((el) => el.getClientRects().length && /^(等待审核|审核中|已申请|申请已提交)$/.test((el.textContent || '').trim())))()`, 'group join result');
    const after = await joinedGroups(page);
    const oldIds = new Set(before.map((row) => row.groupId));
    const added = after.filter((row) => !oldIds.has(row.groupId));
    if (added.length === 1) {
      return [{ status: 'joined', group_id: added[0].groupId,
        group_name: added[0].name, url: `${resolved.origin}${resolved.pathname}` }];
    }
    if (pending) {
      return [{ status: 'pending_approval', group_id: '', group_name: '',
        url: `${resolved.origin}${resolved.pathname}` }];
    }
    throw new CommandExecutionError('Join was attempted but new group membership was not verified; do not retry automatically');
  },
});
