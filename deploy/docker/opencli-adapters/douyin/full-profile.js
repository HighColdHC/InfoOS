export const DOUYIN_PROFILE_PAGE_SIZE = 20;
export const DEFAULT_MAX_PROFILE_PAGES = 500;
export const DEFAULT_CURSOR_REINITIALIZATION_LIMIT = 20;

export function normalizeProfileLimit(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return 0;
  return Math.min(10_000, Math.max(1, Math.round(numeric)));
}

export function normalizeMaxProfilePages(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_MAX_PROFILE_PAGES;
  return Math.min(2_000, Math.max(1, Math.round(numeric)));
}

function validCreateTime(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

export function isPinnedProfileVideo(item, index, pageItems, pageNumber) {
  if (item?.is_top === true || Number(item?.is_top) === 1) return true;
  if (pageNumber !== 1 || index < 0 || index >= 3) return false;
  const createdAt = validCreateTime(item?.create_time);
  if (createdAt === null) return false;
  return pageItems.slice(index + 1).some((later) => {
    const laterCreatedAt = validCreateTime(later?.create_time);
    return laterCreatedAt !== null && laterCreatedAt > createdAt;
  });
}

export async function collectDouyinProfileVideos({
  fetchPage,
  limit = 0,
  maxPages = DEFAULT_MAX_PROFILE_PAGES,
  stopAwemeId = '',
}) {
  const normalizedLimit = normalizeProfileLimit(limit);
  const normalizedMaxPages = normalizeMaxProfilePages(maxPages);
  const stopIds = new Set(String(stopAwemeId || '').split(',').map((id) => id.trim()).filter(Boolean));
  const videos = [];
  const seenAwemeIds = new Set();
  const seenCursors = new Set();
  let cursor = '0';

  for (let pageNumber = 1; pageNumber <= normalizedMaxPages; pageNumber += 1) {
    if (seenCursors.has(cursor)) {
      throw new Error(`Douyin profile cursor stalled before page ${pageNumber}: ${cursor}`);
    }
    seenCursors.add(cursor);

    const payload = await fetchPage(cursor, DOUYIN_PROFILE_PAGE_SIZE);
    const pageItems = Array.isArray(payload?.aweme_list) ? payload.aweme_list : [];
    for (const [index, item] of pageItems.entries()) {
      const awemeId = String(item?.aweme_id || '').trim();
      if (!awemeId || seenAwemeIds.has(awemeId)) continue;
      seenAwemeIds.add(awemeId);

      const isTop = isPinnedProfileVideo(item, index, pageItems, pageNumber);
      if (item?.video?.play_addr?.url_list?.[0]) {
        videos.push({ ...item, is_top: isTop });
      }
      if (!isTop && stopIds.has(awemeId)) {
        return { videos, cursorReinitialized: false };
      }
      if (stopIds.size === 0 && normalizedLimit > 0 && videos.length >= normalizedLimit) {
        return { videos: videos.slice(0, normalizedLimit), cursorReinitialized: false };
      }
    }

    const hasMore = Boolean(Number(payload?.has_more || 0));
    if (!hasMore) {
      return stopIds.size
        ? {
            videos: videos.slice(0, normalizedLimit || DEFAULT_CURSOR_REINITIALIZATION_LIMIT),
            cursorReinitialized: true,
          }
        : { videos, cursorReinitialized: false };
    }

    const nextCursor = String(payload?.max_cursor ?? payload?.cursor ?? '').trim();
    if (!nextCursor || nextCursor === cursor) {
      throw new Error(`Douyin profile cursor did not advance after page ${pageNumber}: ${cursor}`);
    }
    cursor = nextCursor;
  }

  if (stopIds.size > 0) {
    return {
      videos: videos.slice(0, normalizedLimit || DEFAULT_CURSOR_REINITIALIZATION_LIMIT),
      cursorReinitialized: true,
    };
  }
  throw new Error(
    `Douyin profile exceeded the safety limit of ${normalizedMaxPages} pages before completion`,
  );
}
