import { browserFetch } from './browser-fetch.js';

export async function fetchDouyinUserVideoPage(page, secUid, maxCursor, count) {
  const params = new URLSearchParams({
    sec_user_id: secUid,
    max_cursor: String(maxCursor),
    count: String(count),
    aid: '6383',
  });
  return browserFetch(
    page,
    'GET',
    `https://www.douyin.com/aweme/v1/web/aweme/post/?${params.toString()}`,
    { headers: { referer: 'https://www.douyin.com/' } },
  );
}

export async function fetchDouyinUserVideos(page, secUid, count) {
  const data = await fetchDouyinUserVideoPage(page, secUid, 0, count);
  return data.aweme_list || [];
}

export async function fetchDouyinUserProfile(page, secUid) {
  const params = new URLSearchParams({
    sec_user_id: secUid,
    publish_video_strategy_type: '2',
    personal_center_strategy: '1',
    aid: '6383',
  });
  const data = await browserFetch(
    page,
    'GET',
    `https://www.douyin.com/aweme/v1/web/user/profile/other/?${params.toString()}`,
    { headers: { referer: `https://www.douyin.com/user/${secUid}` } },
  );
  return data.user || data.user_info || {};
}

const COMMENT_PAGE_SIZE = 20;

export async function fetchDouyinComments(page, awemeId, count) {
  const comments = [];
  let cursor = 0;
  // The web endpoint pages in small batches, so a 50-comment request needs a cursor walk.
  while (comments.length < count) {
    const params = new URLSearchParams({
      aweme_id: awemeId,
      count: String(Math.min(COMMENT_PAGE_SIZE, count - comments.length)),
      cursor: String(cursor),
      aid: '6383',
    });
    const data = await browserFetch(
      page,
      'GET',
      `https://www.douyin.com/aweme/v1/web/comment/list/?${params.toString()}`,
      { headers: { referer: 'https://www.douyin.com/' } },
    );
    const batch = data.comments || [];
    comments.push(...batch);
    const nextCursor = Number(data.cursor);
    if (batch.length === 0 || !data.has_more || !(nextCursor > cursor)) break;
    cursor = nextCursor;
  }
  return comments.slice(0, count).map((comment) => ({
    text: comment.text || '',
    digg_count: comment.digg_count ?? 0,
    nickname: comment.user?.nickname || '',
  }));
}
