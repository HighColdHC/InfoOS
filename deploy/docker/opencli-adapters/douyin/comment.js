import { cli, Strategy } from '@jackwener/opencli/registry';
import { openVideo, WEB } from './_social.js';
import { commentText, postComment } from './_post-comment.js';

cli({
  site: 'douyin', name: 'comment', access: 'write',
  description: '对抖音视频发表一条评论，返回平台评论 ID', domain: 'www.douyin.com',
  strategy: Strategy.COOKIE, navigateBefore: false,
  args: [
    { name: 'video', required: true, positional: true, help: '视频 ID 或完整 URL' },
    { name: 'text', required: true, positional: true, help: '要发布的评论内容' },
  ],
  columns: ['aweme_id', 'comment_id', 'status', 'url'],
  func: async (page, kwargs) => {
    const id = await openVideo(page, kwargs.video);
    const commentId = await postComment(page, id, commentText(kwargs.text));
    return [{ aweme_id: id, comment_id: commentId, status: 'accepted', url: `${WEB}/video/${id}` }];
  },
});
