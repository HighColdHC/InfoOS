import { cli, Strategy } from '@jackwener/opencli/registry';
import { boundedInt, evaluate, openSignedNote } from './_shared.js';

cli({
  site: 'xiaohongshu', name: 'comment-targets', access: 'read',
  description: '读取笔记上可见评论的 ID，供精确回复；完整文本可用 comments 命令',
  domain: 'www.xiaohongshu.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [
    { name: 'note-url', required: true, positional: true, help: '包含 xsec_token 的笔记 URL' },
    { name: 'limit', type: 'int', default: 20, help: '最多返回 1-50 条可见评论' },
  ],
  columns: ['note_id', 'comment_id', 'parent_id', 'author', 'text', 'url'],
  func: async (page, kwargs) => {
    const identity = await openSignedNote(page, kwargs['note-url']);
    const limit = boundedInt(kwargs.limit, 'limit');
    const rows = await evaluate(page, `(() => {
      const items = [...document.querySelectorAll('.parent-comment')];
      const results = [];
      for (const parent of items) {
        const root = parent.querySelector('.comment-item[id^="comment-"]');
        if (!root) continue;
        const parentId = root.id.slice('comment-'.length);
        results.push({
          comment_id: parentId, parent_id: '',
          author: (root.querySelector('.author-wrapper .name, .user-name')?.textContent || '').trim(),
          text: (root.querySelector('.content, .note-text')?.textContent || '').trim(),
        });
        for (const reply of parent.querySelectorAll('.comment-item-sub[id^="comment-"], .sub-comment-list .comment-item[id^="comment-"]')) {
          results.push({
            comment_id: reply.id.slice('comment-'.length), parent_id: parentId,
            author: (reply.querySelector('.name, .user-name')?.textContent || '').trim(),
            text: (reply.querySelector('.content, .note-text')?.textContent || '').trim(),
          });
        }
      }
      return results;
    })()`, 'comment targets');
    return rows.filter((row) => row.comment_id && row.text).slice(0, limit).map((row) => ({
      note_id: identity.noteId, ...row, url: identity.url,
    }));
  },
});
