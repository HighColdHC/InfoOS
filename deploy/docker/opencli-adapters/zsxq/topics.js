import { cli, Strategy } from '@jackwener/opencli/registry';
import {
  ensureZsxqAuth,
  ensureZsxqPage,
  DEFAULT_GROUP_ID,
  fetchTopicPages,
  createHydrationBudget,
  hydrateTopicRows,
} from './utils.js';

cli({
  site: 'zsxq',
  name: 'topics',
  access: 'read',
  description: '获取当前星球的话题列表',
  domain: 'wx.zsxq.com',
  strategy: Strategy.COOKIE,
  browser: true,
  args: [
    { name: 'limit', type: 'int', default: 20, help: 'Number of topics to return' },
    { name: 'since', help: 'ISO timestamp; return every newer topic within the incremental page cap' },
    { name: 'group_id', help: 'Optional group id; defaults to the active group in Chrome' },
    { name: 'filter', default: 'all', help: 'all or essence' },
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
    const contentType = String(kwargs.filter || 'all') === 'essence' ? 'essence' : 'all';
    const scope = contentType === 'essence' ? 'digests' : 'all';
    const topics = await fetchTopicPages(page, (endTime) => {
      const cursor = endTime ? `&end_time=${encodeURIComponent(endTime)}` : '';
      return `https://api.zsxq.com/v2/groups/${groupId}/topics?scope=${scope}&count=${(kwargs.since ? 30 : Math.min(limit, 30))}${cursor}`;
    }, limit, { since: kwargs.since, chronological: true });
    return hydrateTopicRows(page, (kwargs.since ? topics : topics.slice(0, limit)).map((topic) =>
      scope === 'digests' ? { ...topic, is_digest: true } : topic), budget);
  },
});
