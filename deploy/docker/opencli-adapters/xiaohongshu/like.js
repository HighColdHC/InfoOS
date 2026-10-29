import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { evaluate, openSignedNote } from './_shared.js';

const LIKE_SELECTOR = '.interact-container .left .like-lottie';

function likedStateScript(noteId) {
  return `(() => {
    const map = window.__INITIAL_STATE__?.note?.noteDetailMap || {};
    const detail = map[${JSON.stringify(noteId)}];
    const liked = detail?.note?.interactInfo?.liked;
    return { control: !!document.querySelector(${JSON.stringify(LIKE_SELECTOR)}), liked };
  })()`;
}

cli({
  site: 'xiaohongshu', name: 'like', access: 'write',
  description: '给指定小红书笔记点赞，并验证页面状态',
  domain: 'www.xiaohongshu.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [{ name: 'note-url', required: true, positional: true, help: '包含 xsec_token 的笔记 URL' }],
  columns: ['note_id', 'status', 'url'],
  func: async (page, kwargs) => {
    const identity = await openSignedNote(page, kwargs['note-url']);
    const before = await evaluate(page, likedStateScript(identity.noteId), 'like state');
    if (!before.control || typeof before.liked !== 'boolean') {
      throw new CommandExecutionError('Xiaohongshu note like state is unavailable');
    }
    const receiptUrl = `https://www.xiaohongshu.com/explore/${identity.noteId}`;
    if (before.liked) return [{ note_id: identity.noteId, status: 'already_liked', url: receiptUrl }];
    await page.click(LIKE_SELECTOR);
    await page.wait({ time: 2 });
    const after = await evaluate(page, likedStateScript(identity.noteId), 'like result');
    if (after.liked !== true) throw new CommandExecutionError('Like was attempted but the liked state was not verified; do not retry automatically');
    return [{ note_id: identity.noteId, status: 'liked', url: receiptUrl }];
  },
});
