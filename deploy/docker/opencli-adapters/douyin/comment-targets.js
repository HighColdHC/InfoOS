import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { api, bounded, commentRow, openVideo } from './_social.js';

cli({
  site: 'douyin', name: 'comment-targets', access: 'read',
  description: '读取视频评论和评论 ID，供精确回复', domain: 'www.douyin.com',
  strategy: Strategy.COOKIE, navigateBefore: false,
  args: [
    { name: 'video', required: true, positional: true, help: '视频 ID 或完整 URL' },
    { name: 'limit', type: 'int', default: 20, help: '最多返回 1-50 条' },
    { name: 'cursor', type: 'string', default: '0', help: '评论页游标，默认首页' },
    { name: 'parent-id', type: 'string', default: '', help: '读取指定一级评论下的回复' },
  ],
  columns: ['aweme_id', 'comment_id', 'parent_id', 'author', 'author_url', 'text', 'digg_count', 'reply_count', 'created_at', 'video_url', 'next_cursor', 'has_more'],
  func: async (page, kwargs) => {
    const id = await openVideo(page, kwargs.video);
    const limit = bounded(kwargs.limit, 'limit');
    const cursor = String(kwargs.cursor ?? '0');
    if (!/^\d+$/.test(cursor)) throw new CommandExecutionError('cursor must be numeric');
    const parent = String(kwargs['parent-id'] || '');
    if (parent && !/^\d{15,22}$/.test(parent)) throw new CommandExecutionError('parent-id must be a Douyin comment ID');
    const data = await api(page, parent ? '/aweme/v1/web/comment/list/reply/' : '/aweme/v1/web/comment/list/', {
      aweme_id: id, count: limit, cursor, ...(parent ? { comment_id: parent } : {}),
    });
    if (!Array.isArray(data.comments)) throw new CommandExecutionError('Douyin returned no comment list');
    return data.comments.slice(0, limit).map((comment) => ({
      ...commentRow(comment, id, parent), next_cursor: String(data.cursor ?? ''), has_more: Boolean(data.has_more),
    }));
  },
});
