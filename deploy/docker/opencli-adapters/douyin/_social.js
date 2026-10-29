import { ArgumentError, AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';

export const WEB = 'https://www.douyin.com';

export function unwrap(value) {
  return value && typeof value === 'object' && 'session' in value && 'data' in value ? value.data : value;
}

export async function evaluate(page, source) {
  const value = unwrap(await page.evaluate(source));
  if (value == null) throw new CommandExecutionError('Douyin page returned no result');
  return value;
}

export function bounded(value, name, fallback = 20, maximum = 50) {
  const n = value == null || value === '' ? fallback : Number(value);
  if (!Number.isInteger(n) || n < 1 || n > maximum) throw new ArgumentError(`${name} must be 1-${maximum}`);
  return n;
}

export function videoId(value) {
  const raw = String(value ?? '').trim();
  if (/^\d{15,22}$/.test(raw)) return raw;
  let url;
  try { url = new URL(raw); } catch { throw new ArgumentError('Expected a Douyin video ID or full URL'); }
  const id = url.pathname.match(/^\/video\/(\d{15,22})\/?$/)?.[1];
  if (url.protocol !== 'https:' || url.hostname !== 'www.douyin.com' || !id) {
    throw new ArgumentError('Expected https://www.douyin.com/video/<id>');
  }
  return id;
}

export function commentId(value) {
  const id = String(value ?? '').trim();
  if (!/^\d{15,22}$/.test(id)) throw new ArgumentError('Expected a numeric Douyin comment ID');
  return id;
}

export function profileId(value) {
  const raw = String(value ?? '').trim();
  if (/^MS4wLjAB[A-Za-z0-9_-]{20,}$/.test(raw)) return raw;
  let url;
  try { url = new URL(raw); } catch { throw new ArgumentError('Expected a Douyin sec_uid or profile URL'); }
  const id = url.pathname.match(/^\/user\/(MS4wLjAB[A-Za-z0-9_-]{20,})\/?$/)?.[1];
  if (url.protocol !== 'https:' || url.hostname !== 'www.douyin.com' || !id) {
    throw new ArgumentError('Expected https://www.douyin.com/user/<sec_uid>');
  }
  return id;
}

export async function open(page, url, path) {
  await page.goto(url);
  await page.wait({ time: 1 });
  const current = new URL(await evaluate(page, 'location.href'));
  if (current.hostname !== 'www.douyin.com' || current.pathname.includes('login')) {
    throw new AuthRequiredError('www.douyin.com');
  }
  if (path && current.pathname !== path) {
    throw new CommandExecutionError(`Douyin navigated to ${current.pathname} instead of ${path}`);
  }
  return current;
}

export async function openVideo(page, value) {
  const id = videoId(value);
  await open(page, `${WEB}/video/${id}`, `/video/${id}`);
  return id;
}

export async function api(page, path, params = {}, { method = 'GET', form } = {}) {
  const url = new URL(path, WEB);
  url.searchParams.set('aid', '6383');
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));
  const js = `(async () => {
    try {
      const response = await fetch(${JSON.stringify(url.toString())}, {
        method: ${JSON.stringify(method)}, credentials: 'include',
        ${form ? `headers: {'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'}, body: new URLSearchParams(${JSON.stringify(form)}).toString(),` : ''}
      });
      const body = await response.json();
      return { http: response.status, body };
    } catch (error) { return { http: 0, error: String(error) }; }
  })()`;
  const result = await evaluate(page, js);
  if (result.http !== 200 || !result.body || typeof result.body !== 'object') {
    throw new CommandExecutionError(`Douyin ${path} failed (HTTP ${result.http}); ${method === 'POST' ? 'outcome unknown, do not retry automatically' : 'check login and page access'}`);
  }
  const data = result.body;
  if (data.status_code !== 0) {
    const message = String(data.status_msg || data.message || '').slice(0, 120);
    throw new CommandExecutionError(`Douyin ${path} rejected request (code ${data.status_code ?? 'unknown'}${message ? `: ${message}` : ''})`);
  }
  return data;
}

export function commentRow(comment, awemeId, parentId = '') {
  return {
    aweme_id: awemeId,
    comment_id: String(comment.cid || ''),
    parent_id: parentId || (String(comment.reply_id || '') === '0' ? '' : String(comment.reply_id || '')),
    author: String(comment.user?.nickname || ''),
    author_url: comment.user?.sec_uid ? `${WEB}/user/${comment.user.sec_uid}` : '',
    text: String(comment.text || ''),
    digg_count: Number(comment.digg_count || 0),
    reply_count: Number(comment.reply_comment_total || 0),
    created_at: Number(comment.create_time || 0),
    video_url: `${WEB}/video/${awemeId}`,
  };
}

export async function videoDetail(page, id) {
  const data = await api(page, '/aweme/v1/web/aweme/detail/', { aweme_id: id });
  if (String(data.aweme_detail?.aweme_id || '') !== id) {
    throw new CommandExecutionError('Douyin video detail identity could not be verified');
  }
  return data.aweme_detail;
}
