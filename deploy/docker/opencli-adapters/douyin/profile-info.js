import { cli, Strategy } from '@jackwener/opencli/registry';
import { api, open, profileId, WEB } from './_social.js';

cli({
  site: 'douyin', name: 'profile-info', access: 'read',
  description: '读取抖音作者公开主页简介', domain: 'www.douyin.com',
  strategy: Strategy.COOKIE, navigateBefore: false,
  args: [{ name: 'user', required: true, positional: true, help: 'sec_uid 或完整主页 URL' }],
  columns: ['sec_uid', 'nickname', 'signature', 'follower_count', 'following_count', 'aweme_count', 'url'],
  func: async (page, kwargs) => {
    const id = profileId(kwargs.user);
    const url = `${WEB}/user/${id}`;
    await open(page, url, `/user/${id}`);
    const data = await api(page, '/aweme/v1/web/user/profile/other/', { sec_user_id: id });
    const user = data.user || data.user_info || {};
    return [{
      sec_uid: id, nickname: String(user.nickname || ''), signature: String(user.signature || ''),
      follower_count: Number(user.follower_count || 0), following_count: Number(user.following_count || 0),
      aweme_count: Number(user.aweme_count || 0), url,
    }];
  },
});
