import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { evaluate, openPage, profileIdentity } from './_shared.js';

cli({
  site: 'xiaohongshu', name: 'profile-info', access: 'read',
  description: '读取指定小红书作者主页的简介与公开身份信息',
  domain: 'www.xiaohongshu.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [{ name: 'user', required: true, positional: true, help: '作者用户 ID 或完整主页 URL' }],
  columns: ['user_id', 'nickname', 'description', 'red_id', 'ip_location', 'url'],
  func: async (page, kwargs) => {
    const identity = profileIdentity(kwargs.user);
    await openPage(page, identity.url, `/user/profile/${identity.userId}`);
    const data = await evaluate(page, `(() => {
      const store = window.__INITIAL_STATE__?.user;
      const pageData = store?.userPageData?._value || store?.userPageData;
      const basic = pageData?.basicInfo || {};
      return {
        nickname: String(basic.nickname || '').trim(),
        description: String(basic.desc || document.querySelector('.user-desc')?.textContent || '').trim(),
        redId: String(basic.redId || '').trim(),
        ipLocation: String(basic.ipLocation || '').trim(),
      };
    })()`, 'profile');
    if (!data.nickname && !data.description) {
      throw new CommandExecutionError('Xiaohongshu profile identity and description were not rendered');
    }
    return [{ user_id: identity.userId, nickname: data.nickname, description: data.description,
      red_id: data.redId, ip_location: data.ipLocation, url: identity.url }];
  },
});
