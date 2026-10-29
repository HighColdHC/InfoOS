import { cli, Strategy } from '@jackwener/opencli/registry';
import { CliError } from '@jackwener/opencli/errors';
import {
  browserJsonRequest,
  DEFAULT_GROUP_ID,
  ensureZsxqAuth,
  ensureZsxqPage,
  fetchFirstJson,
  getCommentsFromResponse,
  getTopicIdentity,
  getTopicFromResponse,
  getTopicsFromResponse,
  getTopicUrl,
  createHydrationBudget,
  hydrateTopicRow,
  summarizeComments,
} from './utils.js';

cli({
  site: 'zsxq',
  name: 'topic',
  access: 'read',
  description: '获取单个话题详情和评论',
  domain: 'wx.zsxq.com',
  strategy: Strategy.COOKIE,
  browser: true,
  args: [
    { name: 'id', required: true, positional: true, help: 'Topic ID' },
    { name: 'group_id', help: 'Group ID retained for CLI compatibility' },
    { name: 'comment_limit', type: 'int', default: 20, help: 'Number of comments to fetch' },
  ],
  columns: [
    'topic_id', 'type', 'author', 'title', 'content', 'full_text', 'tags', 'links',
    'images', 'videos', 'files', 'comments', 'likes', 'readers', 'time', 'article_url', 'article_title', 'content_source',
    'original_content', 'external_doc_body', 'content_source_complete', 'content_source_completeness',
    'content_source_blocks', 'content_source_reached_end', 'media_blobs', 'content_source_status', 'content_source_url', 'content_source_title', 'comment_preview', 'url',
  ],
  func: async (page, kwargs) => {
    const budget = createHydrationBudget(kwargs.limit || 1);
    await ensureZsxqPage(page);
    await ensureZsxqAuth(page);
    const topicId = String(kwargs.id);
    const commentLimit = Math.max(1, Number(kwargs.comment_limit) || 20);
    const groupId = String(kwargs.group_id || DEFAULT_GROUP_ID);
    const detailUrl = `https://api.zsxq.com/v2/topics/${topicId}`;
    const detailResp = await browserJsonRequest(page, detailUrl);
    if (detailResp.status === 404) throw new CliError('NOT_FOUND', `Topic ${topicId} not found`);
    if (!detailResp.ok) {
      throw new CliError(
        'FETCH_ERROR',
        detailResp.error || `Failed to fetch topic ${topicId}`,
        `Checked endpoint: ${detailUrl}`,
      );
    }
    let topic;
    try {
      topic = getTopicFromResponse(detailResp.data);
    } catch (error) {
      if (String(error?.code || '') !== '1059') throw error;
      const fallbackResp = await fetchFirstJson(page, [
        `https://api.zsxq.com/v2/groups/${groupId}/topics?scope=all&count=30`,
      ]);
      topic = getTopicsFromResponse(fallbackResp.data)
        .find((candidate) => getTopicIdentity(candidate) === topicId);
    }
    if (!topic) throw new CliError('NOT_FOUND', `Topic ${topicId} not found`);
    let comments = Array.isArray(topic.show_comments) ? topic.show_comments : [];
    try {
      const commentsResp = await fetchFirstJson(page, [
        `https://api.zsxq.com/v2/topics/${topicId}/comments?sort=asc&count=${commentLimit}&with_sticky=true`,
      ]);
      comments = getCommentsFromResponse(commentsResp.data);
    } catch (error) {
      if (String(error?.code || '') !== '1059') throw error;
    }
    const row = await hydrateTopicRow(page, {
      ...topic,
      comments,
      comments_count: topic.comments_count ?? comments.length,
    }, budget);
    return [{
      ...row,
      comment_preview: summarizeComments(comments, 5),
      url: getTopicUrl(topic.topic_uid || topic.topic_id || topicId),
    }];
  },
});
