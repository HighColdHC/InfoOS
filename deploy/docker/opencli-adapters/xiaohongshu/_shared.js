import { ArgumentError, AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';

export const XHS_WEB = 'https://www.xiaohongshu.com';
const XHS_IM = 'https://edith.xiaohongshu.com';

export function unwrap(payload) {
  if (payload && typeof payload === 'object' && 'session' in payload && 'data' in payload) {
    return payload.data;
  }
  return payload;
}

export function boundedInt(value, name, { min = 1, max = 50, fallback = 20 } = {}) {
  if (value === undefined || value === null || value === '') return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    throw new ArgumentError(`${name} must be an integer from ${min} to ${max}`);
  }
  return parsed;
}

export function profileIdentity(value) {
  const raw = String(value ?? '').trim();
  let id = raw;
  if (raw.startsWith('https://')) {
    let url;
    try { url = new URL(raw); } catch { throw new ArgumentError('Invalid Xiaohongshu profile URL'); }
    if (url.hostname !== 'www.xiaohongshu.com') {
      throw new ArgumentError('Profile URL must use www.xiaohongshu.com');
    }
    id = url.pathname.match(/^\/user\/profile\/([a-zA-Z0-9]{8,32})\/?$/)?.[1] || '';
  }
  if (!/^[a-zA-Z0-9]{8,32}$/.test(id)) {
    throw new ArgumentError('Expected a Xiaohongshu user ID or full profile URL');
  }
  return { userId: id, url: `${XHS_WEB}/user/profile/${id}` };
}

export function signedNoteIdentity(value) {
  let url;
  try { url = new URL(String(value ?? '').trim()); } catch { throw new ArgumentError('Expected a full Xiaohongshu note URL'); }
  if (url.protocol !== 'https:' || url.hostname !== 'www.xiaohongshu.com') {
    throw new ArgumentError('Note URL must use https://www.xiaohongshu.com');
  }
  const noteId = url.pathname.match(/^\/(?:explore|note|search_result|discovery\/item)\/([a-f0-9]{16,32})\/?$/i)?.[1]
    || url.pathname.match(/^\/user\/profile\/[a-zA-Z0-9]{8,32}\/([a-f0-9]{16,32})\/?$/i)?.[1];
  if (!noteId || !url.searchParams.get('xsec_token')) {
    throw new ArgumentError('Note URL must include a note ID and xsec_token from search or feed');
  }
  return { noteId, url: url.toString() };
}

export function numericId(value, name) {
  const id = String(value ?? '').trim();
  if (!/^\d{1,32}$/.test(id)) throw new ArgumentError(`${name} must be a numeric ID`);
  return id;
}

export function commentId(value) {
  const id = String(value ?? '').trim();
  if (!/^[a-f0-9]{16,32}$/i.test(id)) {
    throw new ArgumentError('comment-id must be the ID returned by comment-targets');
  }
  return id;
}

export async function evaluate(page, script, context) {
  const result = unwrap(await page.evaluate(script));
  if (result === undefined || result === null) {
    throw new CommandExecutionError(`Xiaohongshu ${context} returned no page result`);
  }
  return result;
}

export async function openPage(page, url, expectedPath) {
  if (!page) throw new CommandExecutionError('InfoOS Chrome browser session is required');
  await page.goto(url);
  await page.wait({ time: 1 });
  const actual = await evaluate(page, '() => location.href', 'navigation');
  let parsed;
  try { parsed = new URL(actual); } catch { throw new CommandExecutionError('Xiaohongshu returned an invalid page URL'); }
  if (parsed.hostname !== 'www.xiaohongshu.com' || parsed.pathname.startsWith('/login')) {
    throw new AuthRequiredError('www.xiaohongshu.com');
  }
  if (expectedPath && parsed.pathname !== expectedPath) {
    throw new CommandExecutionError(`Xiaohongshu navigated to ${parsed.pathname} instead of ${expectedPath}`);
  }
  return parsed;
}

export async function openSignedNote(page, rawUrl) {
  const identity = signedNoteIdentity(rawUrl);
  const current = await openPage(page, identity.url);
  const currentNoteId = current.pathname.match(/^\/(?:explore|note|search_result|discovery\/item)\/([a-f0-9]{16,32})\/?$/i)?.[1]
    || current.pathname.match(/^\/user\/profile\/[a-zA-Z0-9]{8,32}\/([a-f0-9]{16,32})\/?$/i)?.[1];
  if (currentNoteId !== identity.noteId) {
    throw new CommandExecutionError('Xiaohongshu note identity changed during navigation');
  }
  return identity;
}

export async function imGet(page, path, query) {
  const url = new URL(path, XHS_IM);
  for (const [key, value] of Object.entries(query || {})) url.searchParams.set(key, String(value));
  const script = `(async () => {
    try {
      const response = await fetch(${JSON.stringify(url.toString())}, { credentials: 'include' });
      const payload = await response.json();
      return { httpStatus: response.status, payload };
    } catch (error) {
      return { httpStatus: 0, error: String(error) };
    }
  })()`;
  const result = await evaluate(page, script, path);
  if (!result || typeof result !== 'object' || result.httpStatus !== 200) {
    throw new CommandExecutionError(`Xiaohongshu IM ${path} failed (HTTP ${result?.httpStatus ?? 'unknown'})`);
  }
  const payload = result.payload;
  if (!payload || typeof payload !== 'object' || payload.code !== 0 || payload.success === false) {
    throw new CommandExecutionError(`Xiaohongshu IM ${path} rejected the request (code ${payload?.code ?? 'unknown'})`);
  }
  return payload.data;
}
