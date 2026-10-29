import { ArgumentError } from '@jackwener/opencli/errors';
import { collectDocument } from './document-script.js';

export const DEFAULT_DOCUMENT_BUDGET_SECONDS = 40;

const ALLOWED_DOMAINS = ['feishu.cn', 'larksuite.com'];
const MAX_BODY_CHARS = 120_000;
const MAX_MEDIA_URLS = 200;
const MAX_AUTHENTICATED_MEDIA_ITEMS = 20;
const MAX_AUTHENTICATED_MEDIA_BYTES = 32 * 1024 * 1024;
const MAX_AUTHENTICATED_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_AUTHENTICATED_VIDEO_BYTES = 25 * 1024 * 1024;

export function parseFeishuDocumentUrl(raw) {
  let url;
  try {
    url = new URL(String(raw || '').trim());
  } catch {
    throw new ArgumentError('url must be a valid HTTPS Feishu or Lark document URL');
  }
  const hostname = url.hostname.toLowerCase().replace(/\.$/, '');
  const allowed = ALLOWED_DOMAINS.some(
    (domain) => hostname === domain || hostname.endsWith(`.${domain}`),
  );
  if (
    url.protocol !== 'https:'
    || url.username
    || url.password
    || (url.port && url.port !== '443')
    || !allowed
  ) {
    throw new ArgumentError('url must be an HTTPS URL on feishu.cn or larksuite.com');
  }
  return url.toString();
}

export async function readFeishuDocument(page, rawUrl, options = {}) {
  const sourceUrl = parseFeishuDocumentUrl(rawUrl);
  const budgetSeconds = Number(options.budgetSeconds ?? DEFAULT_DOCUMENT_BUDGET_SECONDS);
  if (!Number.isFinite(budgetSeconds) || budgetSeconds <= 0) {
    throw new ArgumentError('budget-seconds must be a positive number');
  }
  const startedAt = Date.now();
  await page.goto(sourceUrl);
  const budgetMs = Math.max(0, budgetSeconds * 1000 - (Date.now() - startedAt));
  const script = `(${collectDocument.toString()})(${JSON.stringify({ budgetMs })})`;
  const payload = unwrapEvaluateResult(await page.evaluate(script));
  return normalizeDocumentPayload(payload, sourceUrl);
}

export function normalizeDocumentPayload(payload, sourceUrl) {
  const value = payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {};
  const body = String(value.body || '').trim();
  const complete = value.complete === true && value.completeness === 'complete'
    && value.reached_end === true
    && !value.login_required && body.length < MAX_BODY_CHARS;
  return {
    complete,
    completeness: complete ? 'complete' : 'partial',
    blocks: Math.max(0, Math.floor(Number(value.blocks) || 0)),
    reached_end: Boolean(value.reached_end),
    url: String(value.url || sourceUrl),
    title: String(value.title || ''),
    body: body.slice(0, MAX_BODY_CHARS),
    login_required: Boolean(value.login_required),
    images: normalizedHttpUrls(value.images),
    videos: normalizedHttpUrls(value.videos),
    media_blobs: normalizedMediaBlobs(value.media_blobs),
  };
}

export function unwrapEvaluateResult(payload) {
  if (
    payload
    && !Array.isArray(payload)
    && typeof payload === 'object'
    && 'session' in payload
    && 'data' in payload
  ) {
    return payload.data;
  }
  return payload;
}

function normalizedHttpUrls(value) {
  if (!Array.isArray(value)) return [];
  const result = [];
  const seen = new Set();
  for (const raw of value) {
    try {
      const url = new URL(String(raw || ''));
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) continue;
      const normalized = url.toString();
      if (seen.has(normalized)) continue;
      seen.add(normalized);
      result.push(normalized);
      if (result.length >= MAX_MEDIA_URLS) break;
    } catch {
      // Invalid document media URLs are not collection facts.
    }
  }
  return result;
}

function normalizedMediaBlobs(value) {
  if (!Array.isArray(value)) return [];
  const result = [];
  const seen = new Set();
  let totalBytes = 0;
  for (const raw of value) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue;
    const url = normalizedTrustedMediaUrl(raw.url);
    const mimeType = String(raw.mime_type || '').split(';', 1)[0].trim().toLowerCase();
    const kind = mimeType.startsWith('image/')
      ? 'image'
      : mimeType.startsWith('video/') ? 'video' : null;
    const encoded = String(raw.base64 || '').trim();
    const decodedBytes = decodedBase64Bytes(encoded);
    const itemLimit = kind === 'image'
      ? MAX_AUTHENTICATED_IMAGE_BYTES
      : MAX_AUTHENTICATED_VIDEO_BYTES;
    if (
      !url
      || !kind
      || seen.has(url)
      || decodedBytes <= 0
      || decodedBytes > itemLimit
      || totalBytes + decodedBytes > MAX_AUTHENTICATED_MEDIA_BYTES
    ) {
      continue;
    }
    result.push({ url, mime_type: mimeType, base64: encoded });
    seen.add(url);
    totalBytes += decodedBytes;
    if (result.length >= MAX_AUTHENTICATED_MEDIA_ITEMS) break;
  }
  return result;
}

function normalizedTrustedMediaUrl(raw) {
  try {
    const url = new URL(String(raw || '').trim());
    const hostname = url.hostname.toLowerCase().replace(/\.$/, '');
    const allowed = ALLOWED_DOMAINS.some(
      (domain) => hostname === domain || hostname.endsWith(`.${domain}`),
    );
    if (
      url.protocol !== 'https:'
      || url.username
      || url.password
      || (url.port && url.port !== '443')
      || !allowed
    ) {
      return '';
    }
    return url.toString();
  } catch {
    return '';
  }
}

function decodedBase64Bytes(value) {
  if (
    typeof value !== 'string'
    || value.length === 0
    || value.length % 4 !== 0
    || !/^[A-Za-z0-9+/]*={0,2}$/.test(value)
  ) {
    return 0;
  }
  const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0;
  return (value.length / 4) * 3 - padding;
}
