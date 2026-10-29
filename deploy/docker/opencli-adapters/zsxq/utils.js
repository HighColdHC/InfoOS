import { normalizeArticleUrl } from './article-script.js';
import { createHydrationBudget, mergeFeishuDocument } from './document-hydration.js';
export { createHydrationBudget } from './document-hydration.js';
import { ArgumentError, AuthRequiredError, CliError } from '@jackwener/opencli/errors';
import { createHash, randomUUID } from 'node:crypto';
import { createZsxqRequestControl } from './request-control.js';
import { readFeishuDocument } from '../feishu/utils.js';

let requestControl;
const SITE_DOMAIN = 'wx.zsxq.com';
const SITE_URL = 'https://wx.zsxq.com';
const FEISHU_DOMAINS = ['feishu.cn', 'larksuite.com'];
const ZSXQ_API_VERSION = '2.95.0';
export const DEFAULT_GROUP_ID = '1824528822';

function asRecord(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

function pickArray(...values) {
  for (const value of values) if (Array.isArray(value)) return value;
  return [];
}

export async function ensureZsxqPage(page) {
  await page.goto(SITE_URL);
}

export async function ensureZsxqAuth(page) {
  try {
    const result = await browserJsonRequest(page, 'https://api.zsxq.com/v2/groups');
    if (!result.ok || !result.data) throw new AuthRequiredError('zsxq.com');
  } catch (error) {
    if (error instanceof AuthRequiredError || error?.code === 'RATE_LIMITED') throw error;
    throw new AuthRequiredError('zsxq.com');
  }
}

export async function getCookieValue(page, name) {
  const cookies = await page.getCookies({ domain: SITE_DOMAIN });
  return cookies.find((cookie) => cookie.name === name)?.value;
}

export async function getActiveGroupId(page) {
  const groupId = unwrapEvaluateResult(await page.evaluate(`
    (() => {
      const target = localStorage.getItem('target_group');
      if (target) {
        try {
          const parsed = JSON.parse(target);
          if (parsed.group_id) return String(parsed.group_id);
        } catch {}
      }
      return null;
    })()
  `));
  if (groupId) return groupId;
  throw new ArgumentError(
    'Cannot determine active group_id',
    'Pass --group_id <id> or open the target 知识星球 page in Chrome first',
  );
}

export async function browserJsonRequest(page, path, control = (requestControl ??= createZsxqRequestControl())) {
  try {
    return await control.run(path, () => signedBrowserJsonRequest(page, path));
  } catch (error) {
    if (error?.code === 'RATE_LIMITED') throw new CliError('RATE_LIMITED', error.message);
    throw error;
  }
}

async function signedBrowserJsonRequest(page, path) {
  const aduid = String(unwrapEvaluateResult(await page.evaluate(`
    (() => {
      let value = localStorage.getItem('XAduid');
      if (!value) {
        value = crypto.randomUUID();
        localStorage.setItem('XAduid', value);
      }
      return value;
    })()
  `)) || '');
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const requestId = randomUUID();
  const signature = createHash('md5')
    .update(`${path.replace(/'/g, '%27')} ${timestamp} ${requestId}`)
    .digest('hex');
  const headers = {
    accept: 'application/json, text/plain, */*',
    'X-Aduid': aduid,
    'X-Request-Id': requestId,
    'X-Signature': signature,
    'X-Timestamp': timestamp,
    'X-Version': ZSXQ_API_VERSION,
  };
  return unwrapEvaluateResult(await page.evaluate(`
    (async () => {
      const path = ${JSON.stringify(path)};
      const headers = ${JSON.stringify(headers)};
      try {
        return await new Promise((resolve) => {
          const xhr = new XMLHttpRequest();
          xhr.open('GET', path, true);
          xhr.withCredentials = true;
          xhr.timeout = 15000;
          xhr.ontimeout = () => resolve({ ok: false, url: path, error: 'ZSXQ request timeout after 15s' });
          Object.entries(headers).forEach(([name, value]) => xhr.setRequestHeader(name, value));
          xhr.onload = () => {
            let parsed = null;
            if (xhr.responseText) {
              try { parsed = JSON.parse(xhr.responseText); }
              catch {}
            }
            resolve({
              ok: xhr.status >= 200 && xhr.status < 300,
              url: path,
              status: xhr.status,
              data: parsed,
              error: xhr.status >= 200 && xhr.status < 300 ? undefined : 'HTTP ' + xhr.status,
            });
          };
          xhr.onerror = () => resolve({ ok: false, url: path, error: 'Network error' });
          xhr.send();
        });
      } catch (error) {
        return { ok: false, url: path, error: String(error && error.message || error) };
      }
    })()
  `));
}

export async function fetchFirstJson(page, paths) {
  let lastFailure = null;
  for (const path of paths) {
    const result = await browserJsonRequest(page, path);
    if (result.ok) return result;
    lastFailure = result;
  }
  if (!lastFailure) {
    throw new CliError('FETCH_ERROR', 'No candidate endpoint returned JSON', `Checked endpoints: ${paths.join(', ')}`);
  }
  throw new CliError(
    'FETCH_ERROR',
    lastFailure.error || 'Failed to fetch ZSXQ API',
    `Checked endpoints: ${paths.join(', ')}`,
  );
}

export function unwrapEvaluateResult(payload) {
  if (payload && !Array.isArray(payload) && typeof payload === 'object' && 'session' in payload && 'data' in payload) {
    return payload.data;
  }
  return payload;
}

export function unwrapRespData(payload) {
  const record = asRecord(payload);
  if (!record) throw new CliError('PARSE_ERROR', 'Invalid ZSXQ API response');
  if (record.succeeded === false) {
    const code = typeof record.code === 'number' ? String(record.code) : 'API_ERROR';
    const message = typeof record.info === 'string'
      ? record.info
      : typeof record.error === 'string' ? record.error : 'ZSXQ API returned an error';
    throw new CliError(code, message);
  }
  return record.resp_data ?? record.data ?? payload;
}

export function getTopicsFromResponse(payload) {
  const data = unwrapRespData(payload);
  if (Array.isArray(data)) return data;
  return pickArray(data.topics, data.list, data.records, data.items, data.search_result);
}

export function getEndTimeFromResponse(payload, topics = []) {
  const data = unwrapRespData(payload);
  const explicit = asRecord(data)?.end_time || asRecord(data)?.cursor?.end_time;
  if (explicit) return String(explicit);
  const last = topics.at(-1);
  return last?.create_time ? String(last.create_time) : '';
}

export async function fetchTopicPages(page, makeUrl, limit, {
  startDate = '', chronological = false, pageBudget = { remaining: 100 }, maxCandidates = Infinity,
  since = '',
} = {}) {
  const { start } = topicDateBounds(startDate);
  const sinceTime = since ? Date.parse(since) : null;
  if (since && !Number.isFinite(sinceTime)) throw new ArgumentError('Invalid --since ISO time');
  const incremental = chronological && sinceTime !== null;
  const target = incremental ? Infinity : limit;
  if (incremental) {
    const configured = String(process.env.INFOOS_ZSXQ_INCREMENTAL_MAX_PAGES ?? '5').trim();
    const pages = /^\+?[0-9]+$/.test(configured) && Number(configured) > 0 ? Math.min(100, Number(configured)) : 5;
    pageBudget = { remaining: Math.min(pageBudget.remaining, pages) };
  }
  const rows = [];
  const seen = new Set();
  let endTime = '';
  let scanned = 0;
  for (let pageNumber = 0; rows.length < target && pageNumber < 100 && pageBudget.remaining > 0 && scanned < maxCandidates; pageNumber += 1) {
    pageBudget.remaining -= 1;
    const { data } = await fetchFirstJson(page, [makeUrl(endTime, Math.min(target - rows.length, maxCandidates - scanned))]);
    const topics = getTopicsFromResponse(data);
    if (!topics.length) break;
    for (const topic of topics.slice(0, maxCandidates - scanned)) {
      const id = getTopicIdentity(topic);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      if (!incremental || Date.parse(topic.create_time) > sinceTime) rows.push(topic);
      if (rows.length >= target) break;
    }
    scanned += topics.length;
    if (incremental && topics.some(topic => Date.parse(topic.create_time) < sinceTime)) break;
    // Only the time-cursor list endpoint is known to be reverse chronological.
    // Keyword search preserves server ranking; an old match cannot end that scan.
    if (chronological && start !== null
      && topics.some(topic => Date.parse(topic.create_time) < start)) break;
    const next = getEndTimeFromResponse(data, topics);
    if (!next || next === endTime) break;
    endTime = next;
  }
  return rows;
}

export function getCommentsFromResponse(payload) {
  const data = unwrapRespData(payload);
  if (Array.isArray(data)) return data;
  return pickArray(data.comments, data.list, data.items);
}

export function getGroupsFromResponse(payload) {
  const data = unwrapRespData(payload);
  if (Array.isArray(data)) return data;
  return pickArray(data.groups, data.list, data.items);
}

export function getTopicFromResponse(payload) {
  const data = unwrapRespData(payload);
  if (Array.isArray(data)) return data[0] ?? null;
  if (
    typeof data.topic_uid === 'string'
    || typeof data.topic_id === 'number'
    || typeof data.topic_id === 'string'
  ) return data;
  const topic = asRecord(data)?.topic;
  return topic && typeof topic === 'object' ? topic : null;
}

export function getTopicAuthor(topic) {
  return topic.owner?.name
    || topic.talk?.owner?.name
    || topic.question?.owner?.name
    || topic.answer?.owner?.name
    || topic.task?.owner?.name
    || topic.solution?.owner?.name
    || '';
}

export function getTopicText(topic) {
  return cleanRichText(topic.title || '') || getTopicContent(topic);
}

export function getTopicContent(topic) {
  const primary = [
    topic.talk?.text,
    topic.question?.text,
    topic.answer?.text,
    topic.task?.text,
    topic.solution?.text,
  ].find((value) => typeof value === 'string' && value.trim());
  return cleanRichText(primary || '');
}

export function getTopicUrl(topicId) {
  return topicId ? `${SITE_URL}/topic/${topicId}` : SITE_URL;
}

export function getTopicIdentity(topic) {
  return String(topic?.topic_uid || topic?.topic_id || '');
}

export function summarizeComments(comments, limit = 3) {
  return comments.slice(0, limit).map((comment) => {
    const author = comment.owner?.name || '匿名';
    const target = comment.repliee?.name ? ` -> ${comment.repliee.name}` : '';
    return `${author}${target}: ${cleanRichText(comment.text || '')}`;
  }).join(' | ');
}

export function toTopicRow(topic) {
  // ZSXQ topic_id values may exceed Number.MAX_SAFE_INTEGER. The dweb payload
  // carries topic_uid as the lossless string identity used by its own routes.
  const topicId = getTopicIdentity(topic);
  const comments = pickArray(topic.show_comments, topic.comments);
  const tags = getTopicTags(topic);
  const media = extractTopicMedia(topic);
  const content = getTopicContent(topic);
  const links = extractTopicLinks(topic);
  const article = topic.talk?.article;
  const articleUrl = [article?.article_url, article?.inline_article_url, ...links]
    .map(normalizeArticleUrl).find(Boolean) || '';
  return {
    topic_id: topicId,
    legacy_topic_id: topic.topic_id ?? '',
    type: topic.type || '',
    group: topic.group?.name || '',
    author: getTopicAuthor(topic),
    title: getTopicText(topic),
    content,
    full_text: content,
    tags,
    is_digest: isDigestTopic(topic),
    links,
    article_url: articleUrl,
    article_title: article?.title || '',
    images: media.images,
    videos: media.videos,
    files: media.files,
    comments: topic.comments_count ?? comments.length ?? 0,
    likes: topic.likes_count ?? 0,
    readers: topic.readers_count ?? topic.reading_count ?? 0,
    time: topic.create_time || '',
    comment_preview: summarizeComments(comments),
    url: getTopicUrl(topicId),
    content_source: 'zsxq',
    content_source_status: articleUrl ? 'partial' : content ? 'fetched' : 'empty',
    content_source_completeness: articleUrl ? 'partial' : content ? 'complete' : 'partial',
    content_source_url: getTopicUrl(topicId),
    content_source_title: getTopicText(topic),
  };
}

export function getTopicTags(topic) {
  const candidates = [
    topic.tags, topic.hashtags, topic.topic_tags, topic.group?.tags,
    topic.talk?.tags, topic.question?.tags, topic.answer?.tags,
  ].flatMap((value) => Array.isArray(value) ? value : []);
  const values = candidates.map((tag) => typeof tag === 'string'
    ? tag
    : tag?.name || tag?.title || tag?.tag_name || '').map((value) => String(value).trim()).filter(Boolean);
  const richTexts = [topic.title, topic.talk?.text, topic.question?.text, topic.answer?.text, topic.task?.text, topic.solution?.text]
    .filter((value) => typeof value === 'string');
  for (const text of richTexts) {
    for (const node of String(text).match(/<e\b[^>]*\/?>/gi) || []) {
      const attributes = parseAttributes(node);
      if (attributes.type === 'hashtag') {
        const label = decodeUrlComponent(attributes.title || attributes.name || '').replace(/^#+|#+$/g, '').trim();
        if (label) values.push(label);
      }
    }
    const cleaned = cleanRichText(text);
    for (const match of cleaned.matchAll(/#+\s*([^#\n]+?)\s*#+/g)) {
      const label = String(match[1] || '').trim();
      if (label) values.push(label);
    }
  }
  return [...new Set(values)];
}

// Variants such as "AI 风向标" count as 风向标 posts too (user decision 2026-10-27).
export function hasWindVaneTag(topic) {
  return getTopicTags(topic).some((tag) => tag.includes('风向标'));
}

export function validateSearchKeywords(keywords, contentType, startDate = '') {
  const wildcard = keywords.some((value) => value.trim() === '*');
  if (wildcard && !['all', 'essence'].includes(contentType)) {
    throw new ArgumentError(
      'Wildcard keyword * is only supported with --content-type all or essence',
      'Use all or essence with --start-date to list topics',
    );
  }
  if (wildcard && !String(startDate).trim()) {
    throw new ArgumentError('Wildcard keyword * requires --start-date');
  }
}

export function topicMatchesAnyKeyword(topic, keywords) {
  if (!keywords.length || keywords.some((value) => value.trim() === '*')) return true;
  const haystack = `${getTopicText(topic)}\n${getTopicContent(topic)}\n${getTopicTags(topic).join(' ')}`
    .toLocaleLowerCase();
  return keywords.some((value) => haystack.includes(value.toLocaleLowerCase()));
}

export function isDigestTopic(topic) {
  return topic.is_digest === true || topic.is_digested === true || topic.digested === true
    || Boolean(topic.digest_time) || getTopicTags(topic).includes('精华');
}

export function topicMatchesContentType(topic, contentType) {
  if (!contentType || contentType === 'all') return true;
  const tags = getTopicTags(topic);
  if (contentType === 'opportunity') return hasWindVaneTag(topic);
  if (contentType === 'essence') return isDigestTopic(topic);
  if (contentType === 'bid') return topic.is_bid === true || tags.includes('中标');
  if (contentType === 'square') return topic.is_square === true || tags.includes('广场');
  if (contentType === 'topic') return !['question', 'answer', 'task'].includes(String(topic.type || '').toLowerCase());
  return true;
}

export function topicDateBounds(startDate = '', endDate = '') {
  const midnight = (value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new ArgumentError('Date must use YYYY-MM-DD');
    const parsed = Date.parse(`${value}T00:00:00+08:00`);
    if (!Number.isFinite(parsed) || new Date(parsed + 8 * 3600000).toISOString().slice(0, 10) !== value) {
      throw new ArgumentError('Invalid calendar date');
    }
    return parsed;
  };
  return { start: startDate ? midnight(startDate) : null,
    end: endDate ? midnight(endDate) + 86400000 : null };
}

export function filterAndSortTopics(topics, { keyword = '', startDate = '', endDate = '', contentType = 'all', sort = 'comprehensive' } = {}) {
  const normalizedKeyword = String(keyword).trim().toLocaleLowerCase();
  const { start, end } = topicDateBounds(startDate, endDate);
  const filtered = topics.filter((topic) => {
    const created = Date.parse(String(topic.create_time || ''));
    if ((start !== null || end !== null) && !Number.isFinite(created)) return false;
    if (start !== null && created < start) return false;
    if (end !== null && created >= end) return false;
    if (!topicMatchesContentType(topic, contentType)) return false;
    if (!normalizedKeyword) return true;
    const haystack = `${getTopicText(topic)}\n${getTopicContent(topic)}\n${getTopicTags(topic).join(' ')}`.toLocaleLowerCase();
    return haystack.includes(normalizedKeyword);
  });
  const numeric = (topic, keys) => keys.reduce((value, key) => value || Number(topic[key] || 0), 0);
  const comparators = {
    newest: (a, b) => Date.parse(b.create_time) - Date.parse(a.create_time),
    earliest: (a, b) => Date.parse(a.create_time) - Date.parse(b.create_time),
    'most-favorited': (a, b) => numeric(b, ['favorites_count', 'favorite_count']) - numeric(a, ['favorites_count', 'favorite_count']),
    'most-commented': (a, b) => numeric(b, ['comments_count']) - numeric(a, ['comments_count']),
    'most-liked': (a, b) => numeric(b, ['likes_count']) - numeric(a, ['likes_count']),
    'most-coins': (a, b) => numeric(b, ['coins_count', 'coin_count']) - numeric(a, ['coins_count', 'coin_count']),
    'recent-coins': (a, b) => String(b.last_coin_time || b.create_time || '').localeCompare(String(a.last_coin_time || a.create_time || '')),
  };
  return comparators[sort] ? filtered.sort(comparators[sort]) : filtered;
}

function extractTopicMedia(topic) {
  const images = [];
  const videos = [];
  const files = [];
  const stack = [topic];
  const seen = new Set();
  while (stack.length) {
    const value = stack.pop();
    if (Array.isArray(value)) stack.push(...value);
    else if (value && typeof value === 'object' && !seen.has(value)) {
      seen.add(value);
      const url = value.original?.url || value.large?.url || value.url || value.download_url || value.file_url;
      const kind = String(value.type || value.mime_type || value.media_type || '').toLowerCase();
      if (typeof url === 'string' && /^https?:\/\//.test(url)) {
        const hostname = (() => { try { return new URL(url).hostname.toLowerCase(); } catch { return ''; } })();
        if (kind.includes('video') || /\.(mp4|mov|m3u8)(?:\?|$)/i.test(url)) videos.push(url);
        else if (kind.includes('image') || hostname === 'images.zsxq.com' || /\.(jpe?g|png|webp|gif)(?:\?|$)/i.test(url)) images.push(url);
        else if (kind.includes('file') || value.file_id || value.name) files.push({ url, name: value.name || value.filename || '', type: value.mime_type || value.type || '' });
      }
      stack.push(...Object.values(value));
    }
  }
  return { images: [...new Set(images)], videos: [...new Set(videos)], files };
}

export async function hydrateTopicRows(page, topics, budget = createHydrationBudget(topics.length)) {
  const rows = [];
  for (const topic of topics) rows.push(await hydrateTopicRow(page, topic, budget));
  return rows;
}

export async function hydrateTopicRow(page, topic, budget = createHydrationBudget(1)) {
  const row = toTopicRow(topic);
  const feishuUrl = row.links.find(isFeishuDocumentUrl);
  if (row.article_url || !feishuUrl) return row;
  const remaining = budget.deadlineMs - Date.now();
  let document = { url: feishuUrl, body: '', complete: false, completeness: 'partial' };
  let skipped = remaining <= 0;
  if (!skipped) {
    try {
      document = await readFeishuDocument(page, feishuUrl, { budgetSeconds: Math.min(40, remaining / 1000) });
    } catch {
      skipped = true;
    }
  }
  return mergeFeishuDocument(row, document, feishuUrl, skipped);
}

export function extractTopicLinks(topic) {
  const values = [];
  const stack = [topic];
  const seenObjects = new Set();
  while (stack.length) {
    const value = stack.pop();
    if (typeof value === 'string') {
      values.push(...extractRichTextLinks(value), ...extractPlainHttpUrls(value));
    } else if (Array.isArray(value)) {
      stack.push(...value);
    } else if (value && typeof value === 'object' && !seenObjects.has(value)) {
      seenObjects.add(value);
      stack.push(...Object.values(value));
    }
  }
  return uniqueHttpUrls(values);
}

function extractRichTextLinks(value) {
  const links = [];
  const nodes = decodeHtmlEntities(String(value || '')).match(/<e\b[^>]*\/?>/gi) || [];
  for (const node of nodes) {
    const attributes = parseAttributes(node);
    if (attributes.type === 'web' && attributes.href) links.push(attributes.href);
  }
  return links;
}

function extractPlainHttpUrls(value) {
  return String(value || '').match(/https?:\/\/[^\s<>"']+/gi) || [];
}

function cleanRichText(value) {
  return decodeHtmlEntities(String(value || ''))
    .replace(/<e\b[^>]*\/?>/gi, (node) => {
      const attributes = parseAttributes(node);
      const label = decodeUrlComponent(attributes.title || attributes.name || '');
      if (attributes.type === 'web') return label || decodeUrlComponent(attributes.href || '');
      if (attributes.type === 'hashtag') return label ? `#${label}#` : '';
      if (attributes.type === 'mention' || attributes.type === '@') return label ? `@${label}` : '';
      return label;
    })
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
}

function parseAttributes(node) {
  const attributes = {};
  const pattern = /([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  let match;
  while ((match = pattern.exec(node)) !== null) {
    attributes[match[1]] = decodeHtmlEntities(match[2] ?? match[3] ?? '');
  }
  return attributes;
}

function uniqueHttpUrls(values) {
  const urls = [];
  const seen = new Set();
  for (const value of values) {
    try {
      const url = new URL(decodeUrlComponent(String(value || '').trim()).replace(/&amp;/gi, '&'));
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) continue;
      const normalized = url.toString();
      if (!seen.has(normalized)) {
        seen.add(normalized);
        urls.push(normalized);
      }
    } catch {}
  }
  return urls;
}

function isFeishuDocumentUrl(value) {
  try {
    const url = new URL(value);
    const hostname = url.hostname.toLowerCase().replace(/\.$/, '');
    return url.protocol === 'https:'
      && FEISHU_DOMAINS.some((domain) => hostname === domain || hostname.endsWith(`.${domain}`));
  } catch {
    return false;
  }
}

function decodeUrlComponent(value) {
  const normalized = String(value || '').replace(/\+/g, '%20');
  try { return decodeURIComponent(normalized); }
  catch { return String(value || ''); }
}

function decodeHtmlEntities(value) {
  return String(value || '')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>');
}
