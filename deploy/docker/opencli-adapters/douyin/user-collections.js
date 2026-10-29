import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { evaluate, open, profileId, WEB } from './_social.js';

cli({
  site: 'douyin', name: 'user-collections', access: 'read',
  description: '读取指定抖音作者主页公开的合集名称与链接', domain: 'www.douyin.com',
  strategy: Strategy.UI, navigateBefore: false,
  args: [{ name: 'author', required: true, positional: true, help: '作者 sec_uid 或完整主页 URL' }],
  columns: ['author_sec_uid', 'collection_id', 'name', 'url'],
  func: async (page, kwargs) => {
    const id = profileId(kwargs.author);
    await open(page, `${WEB}/user/${id}?showSubTab=compilation`, `/user/${id}`);
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const result = await evaluate(page, `(() => {
        const selected = new URL(location.href).searchParams.get('showSubTab') === 'compilation';
        const rows = [...document.querySelectorAll('a[href^="//www.douyin.com/collection/"],a[href^="/collection/"]')]
          .filter(element => element.getClientRects().length)
          .map(element => {
            const url = new URL(element.getAttribute('href'), location.origin);
            const id = url.pathname.match(/^\\/collection\\/(\\d{15,22})(?:\\/|$)/)?.[1];
            return id ? { collection_id: id, name: (element.getAttribute('title') || '').trim(), url: url.origin + '/collection/' + id } : null;
          }).filter(row => row?.name);
        const empty = document.body.innerText.includes('该账号还未发布过合集');
        return { selected, rows, empty };
      })()`);
      if (!result.selected) throw new CommandExecutionError('Douyin author collection tab was not opened');
      if (result.rows.length) return [...new Map(result.rows.map(row => [row.collection_id, row])).values()]
        .map(row => ({ author_sec_uid: id, ...row }));
      if (result.empty) return [];
      await page.wait({ time: 1 });
    }
    throw new CommandExecutionError('Douyin author collections did not load; an empty list cannot be confirmed');
  },
});
