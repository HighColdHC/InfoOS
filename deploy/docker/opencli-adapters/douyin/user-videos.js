import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError, EmptyResultError } from '@jackwener/opencli/errors';
import {
  fetchDouyinComments,
  fetchDouyinUserProfile,
  fetchDouyinUserVideoPage,
} from './_shared/public-api.js';
import {
  collectDouyinProfileVideos,
  DEFAULT_MAX_PROFILE_PAGES,
  normalizeMaxProfilePages,
  normalizeProfileLimit,
} from './full-profile.js';

function publishedAt(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  return new Date(seconds * 1000).toISOString();
}

function mapVideo(video, index, topComments = [], profile = {}) {
  const author = { ...(video?.author || {}), ...profile };
  const playUrl = video?.video?.play_addr?.url_list?.[0] || '';
  const coverUrl = video?.video?.cover?.url_list?.[0] || '';
  return {
    index: index + 1,
    aweme_id: String(video?.aweme_id || ''),
    is_top: video?.is_top === true,
    title: video?.desc || '',
    duration: Math.round((video?.video?.duration || 0) / 1000),
    digg_count: video?.statistics?.digg_count || 0,
    comment_count: video?.statistics?.comment_count || 0,
    share_count: video?.statistics?.share_count || 0,
    play_url: playUrl,
    cover_url: coverUrl,
    author: author.nickname || '',
    author_handle: author.unique_id || author.short_id || '',
    author_url: author.sec_uid ? `https://www.douyin.com/user/${author.sec_uid}` : '',
    author_signature: author.signature || '',
    follower_count: author.follower_count || 0,
    following_count: author.following_count || 0,
    author_aweme_count: author.aweme_count || 0,
    total_favorited: author.total_favorited || 0,
    top_comments: topComments,
    published_at: publishedAt(video?.create_time),
  };
}

cli({
  site: 'douyin',
  name: 'user-videos',
  access: 'read',
  description: '分页获取指定用户的全量视频，或抓取游标之后的新视频',
  domain: 'www.douyin.com',
  strategy: Strategy.COOKIE,
  args: [
    { name: 'sec_uid', type: 'string', required: true, positional: true, help: '用户 sec_uid' },
    { name: 'limit', type: 'int', default: 0, help: '最多返回数量；0 表示全量' },
    {
      name: 'stop_aweme_id',
      type: 'string',
      default: '',
      help: '遇到任一非置顶视频 ID 后停止；多个 ID 用逗号分隔',
    },
    {
      name: 'max_pages',
      type: 'int',
      default: DEFAULT_MAX_PROFILE_PAGES,
      help: '全量抓取安全页数上限',
    },
    {
      name: 'with_comments',
      type: 'bool',
      default: false,
      help: '是否为每条视频抓取热门评论',
    },
    {
      name: 'comment_limit',
      type: 'int',
      default: 10,
      help: '每条视频最多抓取的热门评论数',
    },
  ],
  columns: [
    'index',
    'aweme_id',
    'is_top',
    'title',
    'duration',
    'digg_count',
    'comment_count',
    'share_count',
    'play_url',
    'cover_url',
    'author',
    'author_handle',
    'author_url',
    'author_signature',
    'follower_count',
    'following_count',
    'author_aweme_count',
    'total_favorited',
    'top_comments',
    'published_at',
    'cursor_reinitialized',
  ],
  func: async (page, kwargs) => {
    const secUid = String(kwargs.sec_uid || '').trim();
    await page.goto(`https://www.douyin.com/user/${secUid}`);
    await page.wait(3);

    let videos;
    let cursorReinitialized;
    try {
      ({ videos, cursorReinitialized } = await collectDouyinProfileVideos({
        fetchPage: (cursor, count) => fetchDouyinUserVideoPage(page, secUid, cursor, count),
        limit: normalizeProfileLimit(kwargs.limit),
        maxPages: normalizeMaxProfilePages(kwargs.max_pages),
        stopAwemeId: kwargs.stop_aweme_id,
      }));
    } catch (error) {
      throw new CommandExecutionError(
        `Douyin full-profile pagination failed: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    }

    if (videos.length === 0 && !String(kwargs.stop_aweme_id || '').trim()) {
      throw new EmptyResultError(
        'douyin user-videos',
        `No videos were returned for sec_uid ${secUid}. Confirm the user exists and the Douyin session is valid.`,
      );
    }
    const withComments = Boolean(kwargs.with_comments);
    const commentLimit = Math.min(50, Math.max(1, Number(kwargs.comment_limit) || 10));
    const rows = [];
    const profile = await fetchDouyinUserProfile(page, secUid);
    for (let index = 0; index < videos.length; index += 1) {
      const video = videos[index];
      let topComments = [];
      if (withComments) {
        topComments = await fetchDouyinComments(page, String(video?.aweme_id || ''), commentLimit);
      }
      rows.push({
        ...mapVideo(video, index, topComments, profile),
        ...(cursorReinitialized ? { cursor_reinitialized: true } : {}),
      });
    }
    return rows;
  },
});
