import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError } from '@jackwener/opencli/errors';
import { evaluate, openPage, profileIdentity } from './_shared.js';
import { profileSnapshot, pageRisk } from './_screening.js';

export async function readProfiles(page, users, wait = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  if (!Array.isArray(users) || users.some(u => typeof u !== 'string')) throw new ArgumentError('Expected user ID array');
  // Validate all identities before navigation; hard cap is independent of caller options.
  const identities = [...new Set(users)].slice(0, 20).map(profileIdentity);
  const output = [];
  let stopped = false;
  for (const identity of identities) {
    const row = { user_id: identity.userId, profile_checked: false, profile_ok: false, pinned_ok: false };
    if (!stopped) {
      if (output.length) await wait(3000);
      row.profile_checked = true;
      try {
        await openPage(page, identity.url, `/user/profile/${identity.userId}`);
        if (await evaluate(page, `(${pageRisk})()`, 'profile risk')) {
          stopped = true;
          row.error = 'login_or_risk';
        } else {
          Object.assign(row, await evaluate(page, `(${profileSnapshot})()`, 'screening profile'));
        }
      } catch {
        // Navigation/bridge failure may hide a challenge. Do not continue browsing or retry the batch.
        stopped = true;
        row.error = 'profile_unavailable';
      }
    } else row.error = 'batch_stopped';
    output.push(row);
  }
  return output;
}

cli({
  site: 'xiaohongshu', name: 'screening-profiles', access: 'read',
  description: '初筛专用：单进程顺序读取至多 20 位作者主页，间隔至少 3 秒',
  domain: 'www.xiaohongshu.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [{ name: 'users', required: true, positional: true, help: '作者 ID 的 JSON 数组' }],
  columns: ['user_id', 'profile_checked', 'profile_ok', 'description', 'pinned_ok', 'pinned_titles', 'group_entry', 'shop_entry', 'error'],
  func: (page, kwargs) => readProfiles(page, JSON.parse(kwargs.users)),
});
