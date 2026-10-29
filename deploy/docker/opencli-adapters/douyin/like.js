import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { api, openVideo, videoDetail, WEB } from './_social.js';

cli({
  site: 'douyin', name: 'like', access: 'write',
  description: '给指定抖音视频点赞一次，读取视频状态确认', domain: 'www.douyin.com',
  strategy: Strategy.COOKIE, navigateBefore: false,
  args: [{ name: 'video', required: true, positional: true, help: '视频 ID 或完整 URL' }],
  columns: ['aweme_id', 'status', 'url'],
  func: async (page, kwargs) => {
    const id = await openVideo(page, kwargs.video);
    const before = await videoDetail(page, id);
    if (Number(before.user_digged) === 1) {
      return [{ aweme_id: id, status: 'already_liked', url: `${WEB}/video/${id}` }];
    }
    if (Number(before.user_digged) !== 0) throw new CommandExecutionError('Douyin like state is unavailable');
    await api(page, '/aweme/v1/web/commit/item/digg/', {}, {
      method: 'POST', form: { aweme_id: id, item_type: '0', type: '1' },
    });
    await page.wait({ time: 1 });
    const after = await videoDetail(page, id);
    if (Number(after.user_digged) !== 1) {
      throw new CommandExecutionError('Like was submitted but liked state was not verified; do not retry automatically');
    }
    return [{ aweme_id: id, status: 'liked', url: `${WEB}/video/${id}` }];
  },
});
