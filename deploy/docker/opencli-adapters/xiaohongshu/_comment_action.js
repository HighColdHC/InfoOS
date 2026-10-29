import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import { evaluate } from './_shared.js';

const EDITOR = '#content-textarea[contenteditable="true"]';

export function commentText(value) {
  const text = String(value ?? '').trim();
  if (!text) throw new ArgumentError('Comment text cannot be empty');
  return text;
}

export async function submitComment(page, text, expectedParentId = '') {
  const before = await evaluate(page, `(() => [...document.querySelectorAll('[id^="comment-"]')].map(e => e.id))()`, 'comment baseline');
  await page.click(EDITOR);
  await page.typeText(EDITOR, text);
  const draft = await evaluate(page, `(() => (document.querySelector(${JSON.stringify(EDITOR)})?.textContent || '').trim())()`, 'comment draft');
  if (draft !== text) throw new CommandExecutionError('Comment editor did not retain the requested text');
  const ready = await evaluate(page, `(() => !!document.querySelector('button.submit:not([disabled])'))()`, 'comment submit control');
  if (!ready) throw new CommandExecutionError('Comment submit control is unavailable');
  await page.installInterceptor('/api/sns/web/v1/comment/post');
  await page.click('button.submit:not([disabled])');
  await page.wait({ time: 2 });
  const responses = await page.getInterceptedRequests();
  const server = responses.at(-1);
  if (!server || typeof server !== 'object') {
    throw new CommandExecutionError('Comment submit was attempted but no platform response was captured; outcome unknown, do not retry automatically');
  }
  if (server.code !== 0 || server.success === false) {
    throw new CommandExecutionError(`Xiaohongshu rejected the comment (code ${server.code ?? 'unknown'}); do not retry automatically`);
  }
  const serverId = server.data?.comment?.id || server.data?.comment?.comment_id
    || server.data?.comment_id || server.data?.id || server.data?.commentId;
  if (serverId) return String(serverId);
  const result = await evaluate(page, `(() => {
    const before = new Set(${JSON.stringify(before)});
    const text = ${JSON.stringify(text)};
    const parentId = ${JSON.stringify(expectedParentId)};
    const roots = parentId ? [document.querySelector('#comment-' + CSS.escape(parentId))?.closest('.parent-comment')].filter(Boolean) : [...document.querySelectorAll('.parent-comment')];
    for (const root of roots) {
      for (const item of root.querySelectorAll('[id^="comment-"]')) {
        if (before.has(item.id)) continue;
        if ((item.querySelector('.content, .note-text')?.textContent || '').trim() === text) {
          return { verified: true, commentId: item.id.slice('comment-'.length) };
        }
      }
    }
    return { verified: false };
  })()`, 'comment result');
  if (!result.verified) {
    throw new CommandExecutionError('Platform accepted the comment, but no comment ID could be read back; do not retry automatically');
  }
  return result.commentId;
}
