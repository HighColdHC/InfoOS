import { cli, Strategy } from '@jackwener/opencli/registry';
import {
  ensureZsxqAuth,
  ensureZsxqPage,
  DEFAULT_GROUP_ID,
  fetchFirstJson,
  getGroupsFromResponse,
  fetchTopicPages,
  filterAndSortTopics,
  getEndTimeFromResponse,
  getTopicIdentity,
  getTopicsFromResponse,
  createHydrationBudget,
  hydrateTopicRows,
  toTopicRow,
  topicDateBounds,
  topicMatchesAnyKeyword,
  validateSearchKeywords,
} from './utils.js';

export function getInitialEndTime(endDate) {
  if (typeof endDate !== 'string' || endDate.length !== 10 || !/^\d{4}-\d{2}-\d{2}$/.test(endDate)) return '';
  // Live-verified list cursor; the exclusive next-midnight bound is enforced by topicDateBounds.
  return `${endDate}T23:59:59.999+0800`;
}

export async function fetchListedTopics(page, groupId, {
  keywords, startDate, endDate, sort, limit, scope = 'digests',
}) {
  const { start, end } = topicDateBounds(startDate, endDate);
  const topics = [];
  const seen = new Set();
  let endTime = getInitialEndTime(endDate);
  for (let pageIndex = 0; pageIndex < 40; pageIndex += 1) {
    const cursor = endTime ? `&end_time=${encodeURIComponent(endTime)}` : '';
    const { data } = await fetchFirstJson(page, [
      `https://api.zsxq.com/v2/groups/${groupId}/topics?scope=${scope}&count=30${cursor}`,
    ]);
    const pageTopics = getTopicsFromResponse(data);
    if (!pageTopics.length) break;
    for (const topic of pageTopics) {
      const id = getTopicIdentity(topic);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const created = Date.parse(String(topic.create_time || ''));
      if (!Number.isFinite(created)) continue;
      if (start !== null && created < start) continue;
      if (end !== null && created >= end) continue;
      if (topicMatchesAnyKeyword(topic, keywords)) topics.push(scope === 'digests' ? { ...topic, is_digest: true } : topic);
    }
    const oldestTime = pageTopics.reduce((oldest, topic) => {
      const created = Date.parse(String(topic.create_time || ''));
      return Number.isFinite(created) ? Math.min(oldest, created) : oldest;
    }, Infinity);
    if (
      topics.length >= limit
      && ['comprehensive', 'newest'].includes(sort)
    ) break;
    // Existing list fixtures use descending create_time with an end_time cursor.
    if (start !== null && oldestTime < start) break;
    const next = getEndTimeFromResponse(data, pageTopics);
    if (!next || next === endTime) break;
    endTime = next;
  }
  return topics;
}

cli({
  site: 'zsxq',
  name: 'search',
  access: 'read',
  description: '搜索星球内容',
  domain: 'wx.zsxq.com',
  strategy: Strategy.COOKIE,
  browser: true,
  args: [
    { name: 'keyword', required: true, positional: true, help: 'Search keyword' },
    { name: 'limit', type: 'int', default: 20, help: 'Number of results to return' },
    { name: 'group_id', help: 'Optional group id; defaults to the active group in Chrome' },
    { name: 'keywords-json', help: 'JSON array of additional keywords' },
    { name: 'start-date', help: 'Inclusive start date (YYYY-MM-DD)' },
    { name: 'end-date', help: 'Inclusive end date (YYYY-MM-DD)' },
    { name: 'content-type', default: 'all', help: 'all, essence, bid, topic, opportunity, or square' },
    { name: 'hydrate', default: 'true', help: 'false: list metadata without reading external documents' },
    { name: 'sort', default: 'comprehensive', help: 'Client-side result ordering' },
  ],
  columns: [
    'is_digest', 'topic_id', 'group', 'type', 'author', 'title', 'content', 'full_text', 'tags', 'links',
    'images', 'videos', 'files', 'comments', 'likes', 'readers', 'time',
    'article_url', 'article_title', 'content_source', 'original_content', 'external_doc_body', 'content_source_complete', 'content_source_completeness',
    'content_source_blocks', 'content_source_reached_end', 'media_blobs', 'content_source_status', 'content_source_url', 'content_source_title', 'url',
  ],
  func: async (page, kwargs) => {
    const hydrate = String(kwargs.hydrate ?? 'true').toLowerCase() !== 'false';
    const keyword = String(kwargs.keyword || '').trim();
    const limit = Math.max(1, Number(kwargs.limit) || 20);
    const groupId = String(kwargs.group_id || DEFAULT_GROUP_ID);
    let keywords = [keyword];
    if (kwargs['keywords-json']) {
      const parsed = JSON.parse(String(kwargs['keywords-json']));
      if (Array.isArray(parsed)) keywords = parsed.map(String).map((value) => value.trim()).filter(Boolean);
    }
    const contentType = String(kwargs['content-type'] || 'all');
    validateSearchKeywords([keyword, ...keywords], contentType, kwargs['start-date']);
    if (keyword === '*' && !keywords.includes('*')) keywords.push('*');
    await ensureZsxqPage(page);
    await ensureZsxqAuth(page);
    let groupName = groupId;
    try {
      const { data: groupsData } = await fetchFirstJson(page, ['https://api.zsxq.com/v2/groups']);
      const found = getGroupsFromResponse(groupsData)
        .find((group) => String(group.group_id) === groupId);
      if (found?.name) groupName = found.name;
    } catch (error) {
      if (error?.code === 'RATE_LIMITED') throw error;
    }
    if (contentType === 'essence' || keywords.includes('*')) {
      const topics = await fetchListedTopics(
        page,
        groupId,
        {
          keywords,
          scope: contentType === 'essence' ? 'digests' : 'all',
          startDate: String(kwargs['start-date'] || ''),
          endDate: String(kwargs['end-date'] || ''),
          sort: String(kwargs.sort || 'comprehensive'),
          limit,
        },
      );
      const filtered = filterAndSortTopics(
        topics,
        {
          startDate: String(kwargs['start-date'] || ''),
          endDate: String(kwargs['end-date'] || ''),
          sort: String(kwargs.sort || 'comprehensive'),
        },
      ).slice(0, limit);
      const rows = hydrate ? await hydrateTopicRows(page, filtered, createHydrationBudget(limit)) : filtered.map(toTopicRow);
      return rows.map((row) => ({ ...row, group: groupName }));
    }
    const collected = [];
    const seen = new Set();
    let remaining = limit;
    const pageBudget = { remaining: Math.ceil(limit / 30) + Math.min(keywords.length, limit) - 1 };
    for (const [index, currentKeyword] of keywords.entries()) {
      if (remaining <= 0 || pageBudget.remaining <= 0) break;
      const query = encodeURIComponent(currentKeyword);
      // Allocate a shared candidate budget fairly; filtering may yield fewer results.
      const pageLimit = Math.ceil(remaining / (keywords.length - index));
      const topics = await fetchTopicPages(page, (endTime, remaining) => {
        const cursor = endTime ? `&end_time=${encodeURIComponent(endTime)}` : '';
        return `https://api.zsxq.com/v2/search/groups/${groupId}/topics?keyword=${query}&count=${Math.min(remaining, 30)}${cursor}`;
      }, pageLimit, { startDate: String(kwargs['start-date'] || ''), pageBudget, maxCandidates: pageLimit });
      remaining -= pageLimit;
      for (const topic of filterAndSortTopics(topics, {
        keyword: currentKeyword,
        startDate: String(kwargs['start-date'] || ''),
        endDate: String(kwargs['end-date'] || ''),
        contentType,
        sort: String(kwargs.sort || 'comprehensive'),
      })) {
        const id = String(topic.topic_uid || topic.topic_id || '');
        if (!id || seen.has(id)) continue;
        seen.add(id);
        collected.push(topic);
      }
    }
    const sorted = filterAndSortTopics(collected, { sort: String(kwargs.sort || 'comprehensive') }).slice(0, limit);
    const rows = hydrate ? await hydrateTopicRows(page, sorted, createHydrationBudget(limit)) : sorted.map(toTopicRow);
    return rows.map((row) => ({ ...row, group: groupName }));
  },
});
