import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { evaluate, open, profileId, WEB } from './_social.js';
import { groups, openMessages } from './_groups.js';

cli({
  site: 'douyin', name: 'group-join', access: 'write',
  description: '从作者主页唯一可见的网页入群入口加入群聊', domain: 'www.douyin.com',
  strategy: Strategy.UI, navigateBefore: false,
  args: [{ name: 'author', required: true, positional: true, help: '作者 sec_uid 或完整主页 URL' }],
  columns: ['status', 'group_name', 'author_url'],
  func: async (page, kwargs) => {
    const id = profileId(kwargs.author);
    const authorUrl = `${WEB}/user/${id}`;
    await openMessages(page);
    const before = new Set((await groups(page)).map((row) => row.name));
    await open(page, authorUrl, `/user/${id}`);
    const controls = await evaluate(page, `(() => [...document.querySelectorAll('button,[role="button"],a')]
      .filter((element) => element.getClientRects().length && /^(加入群聊|加入粉丝群|进群聊)$/.test(element.textContent.trim()))
      .map((element) => ({ text: element.textContent.trim(), tag: element.tagName })))()`);
    if (!Array.isArray(controls) || controls.length !== 1) {
      throw new CommandExecutionError('Author profile has no unique visible web group join control; no join was attempted');
    }
    const clicked = await evaluate(page, `(() => {
      const controls = [...document.querySelectorAll('button,[role="button"],a')]
        .filter((element) => element.getClientRects().length && /^(加入群聊|加入粉丝群|进群聊)$/.test(element.textContent.trim()));
      if (controls.length !== 1) return false;
      controls[0].click();
      return true;
    })()`);
    if (!clicked) throw new CommandExecutionError('Douyin group join control changed before click');
    await page.wait({ time: 2 });
    await openMessages(page);
    const added = (await groups(page)).filter((row) => !before.has(row.name));
    if (added.length !== 1) {
      throw new CommandExecutionError('Group join was attempted but membership could not be verified; do not retry automatically');
    }
    return [{ status: 'joined', group_name: added[0].name, author_url: authorUrl }];
  },
});
