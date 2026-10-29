import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError } from '@jackwener/opencli/errors';
import {
  ensureZsxqAuth,
  ensureZsxqPage,
  DEFAULT_GROUP_ID,
  fetchFirstJson,
  fetchTopicPages,
  filterAndSortTopics,
  unwrapRespData,
  createHydrationBudget,
  hydrateTopicRows,
} from './utils.js';

cli({
  site: 'zsxq',
  name: 'opportunity',
  access: 'read',
  description: '获取群菜单“风向标”标签页的帖子',
  domain: 'wx.zsxq.com',
  strategy: Strategy.COOKIE,
  browser: true,
  args: [
    { name: 'limit', type: 'int', default: 20, help: 'Number of results to return' },
    { name: 'since', help: 'ISO timestamp; return every newer topic within the incremental page cap' },
    { name: 'group_id', help: 'Optional group id' },
    { name: 'keyword', help: 'Optional client-side keyword filter' },
  ],
  columns: [
    'is_digest', 'topic_id', 'type', 'author', 'title', 'content', 'full_text', 'tags', 'links',
    'images', 'videos', 'files', 'comments', 'likes', 'readers', 'time',
    'article_url', 'article_title', 'content_source', 'original_content', 'external_doc_body', 'content_source_complete', 'content_source_completeness',
    'content_source_blocks', 'content_source_reached_end', 'media_blobs', 'content_source_status', 'content_source_url', 'content_source_title', 'url',
  ],
  func: async (page, kwargs) => {
    const budget = createHydrationBudget(kwargs.limit || 20);
    await ensureZsxqPage(page);
    await ensureZsxqAuth(page);
    const limit = Math.max(1, Number(kwargs.limit) || 20);
    const groupId = String(kwargs.group_id || DEFAULT_GROUP_ID);
    const { data } = await fetchFirstJson(page, [`https://api.zsxq.com/v2/groups/${groupId}/menus`]);
    const menus = unwrapRespData(data);
    const hashtagIds = [...new Set([...menus.menus, ...(menus.optional_menus ?? [])]
      .filter(menu => menu.hashtag?.hashtag_id && menu.title.replace(/^[#\s]+|[#\s]+$/g, '').includes('风向标'))
      .map(menu => String(menu.hashtag.hashtag_id)))];
    if (!hashtagIds.length) throw new ArgumentError('未在群菜单中找到风向标标签');
    const topics = await fetchTopicPages(page, (endTime) => {
      const cursor = endTime ? `&end_time=${encodeURIComponent(endTime)}` : '';
      const path = hashtagIds.length === 1
        ? `hashtags/${hashtagIds[0]}/topics?count=30`
        : `hashtags/topics?count=30&tag_ids=${hashtagIds.join(',')}`;
      return `https://api.zsxq.com/v2/${path}${cursor}`;
    }, limit, { since: kwargs.since, chronological: true });
    const filtered = filterAndSortTopics(topics, { keyword: String(kwargs.keyword || ''), sort: 'newest' });
    return hydrateTopicRows(page, (kwargs.since ? filtered : filtered.slice(0, limit)), budget);
  },
});
