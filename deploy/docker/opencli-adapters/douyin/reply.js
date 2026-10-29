import { cli, Strategy } from '@jackwener/opencli/registry';
import { api, commentId, openVideo, WEB } from './_social.js';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { commentText, postComment } from './_post-comment.js';

cli({
  site: 'douyin', name: 'reply', access: 'write',
  description: '回复指定抖音视频的一级评论', domain: 'www.douyin.com',
  strategy: Strategy.COOKIE, navigateBefore: false,
  args: [
    { name: 'video', required: true, positional: true, help: '视频 ID 或完整 URL' },
    { name: 'comment-id', required: true, positional: true, help: 'comment-targets 返回的一级评论 ID' },
    { name: 'text', required: true, positional: true, help: '要发布的回复内容' },
  ],
  columns: ['aweme_id', 'parent_id', 'comment_id', 'status', 'url'],
  func: async (page, kwargs) => {
    const id = await openVideo(page, kwargs.video);
    const parent = commentId(kwargs['comment-id']);
    const data = await api(page, '/aweme/v1/web/comment/list/', { aweme_id: id, count: 50, cursor: 0 });
    if (!Array.isArray(data.comments) || !data.comments.some((comment) => String(comment.cid) === parent)) {
      throw new CommandExecutionError('Target comment is not in the visible first page; reply was not submitted');
    }
    const replyId = await postComment(page, id, commentText(kwargs.text), parent);
    return [{ aweme_id: id, parent_id: parent, comment_id: replyId, status: 'accepted', url: `${WEB}/video/${id}` }];
  },
});
