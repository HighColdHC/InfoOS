import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { commentText, submitComment } from './_comment_action.js';
import { commentId, evaluate, openSignedNote } from './_shared.js';

cli({
  site: 'xiaohongshu', name: 'reply', access: 'write',
  description: '精确回复一条小红书评论并读回新评论 ID',
  domain: 'www.xiaohongshu.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [
    { name: 'note-url', required: true, positional: true, help: '包含 xsec_token 的笔记 URL' },
    { name: 'comment-id', required: true, positional: true, help: 'comment-targets 返回的评论 ID' },
    { name: 'text', required: true, positional: true, help: '回复正文' },
  ],
  columns: ['note_id', 'parent_comment_id', 'comment_id', 'status', 'url'],
  func: async (page, kwargs) => {
    const parentId = commentId(kwargs['comment-id']);
    const text = commentText(kwargs.text);
    const identity = await openSignedNote(page, kwargs['note-url']);
    const selector = `#comment-${parentId} .reply`;
    const found = await evaluate(page, `(() => !!document.querySelector(${JSON.stringify(selector)}))()`, 'reply target');
    if (!found) throw new CommandExecutionError(`Xiaohongshu comment ${parentId} is not visible on this note`);
    await page.click(selector);
    const targeted = await evaluate(page, `(() => {
      const parent = document.querySelector('#comment-' + CSS.escape(${JSON.stringify(parentId)}));
      const targetText = (parent?.querySelector('.content, .note-text')?.textContent || '').trim();
      const replyText = (document.querySelector('.reply-content .content')?.textContent || '').trim();
      return !!targetText && targetText === replyText;
    })()`, 'reply composer');
    if (!targeted) throw new CommandExecutionError('Reply composer is not bound to the requested comment');
    const postedId = await submitComment(page, text, parentId);
    return [{ note_id: identity.noteId, parent_comment_id: parentId, comment_id: postedId,
      status: 'accepted', url: `https://www.xiaohongshu.com/explore/${identity.noteId}` }];
  },
});
