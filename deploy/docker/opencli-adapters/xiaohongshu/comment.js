import { cli, Strategy } from '@jackwener/opencli/registry';
import { openSignedNote } from './_shared.js';
import { commentText, submitComment } from './_comment_action.js';

cli({
  site: 'xiaohongshu', name: 'comment', access: 'write',
  description: '在指定小红书笔记发表评论并读回评论 ID',
  domain: 'www.xiaohongshu.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [
    { name: 'note-url', required: true, positional: true, help: '包含 xsec_token 的笔记 URL' },
    { name: 'text', required: true, positional: true, help: '评论正文' },
  ],
  columns: ['note_id', 'comment_id', 'status', 'url'],
  func: async (page, kwargs) => {
    const text = commentText(kwargs.text);
    const identity = await openSignedNote(page, kwargs['note-url']);
    const commentId = await submitComment(page, text);
    return [{ note_id: identity.noteId, comment_id: commentId, status: 'accepted',
      url: `https://www.xiaohongshu.com/explore/${identity.noteId}` }];
  },
});
