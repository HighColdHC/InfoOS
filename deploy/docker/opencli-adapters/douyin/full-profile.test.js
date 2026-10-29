import assert from 'node:assert/strict';
import test from 'node:test';

import {
  collectDouyinProfileVideos,
  DEFAULT_CURSOR_REINITIALIZATION_LIMIT,
  isPinnedProfileVideo,
  normalizeMaxProfilePages,
  normalizeProfileLimit,
} from './full-profile.js';

const video = (id) => ({
  aweme_id: String(id),
  video: { play_addr: { url_list: [`https://video.example/${id}.mp4`] } },
});

test('collects every page and removes duplicate aweme ids', async () => {
  const pages = {
    0: { aweme_list: [video(3), video(2)], has_more: 1, max_cursor: 10 },
    10: { aweme_list: [video(2), video(1)], has_more: 0, max_cursor: 20 },
  };
  const { videos: rows, cursorReinitialized } = await collectDouyinProfileVideos({
    fetchPage: async (cursor) => pages[cursor],
  });
  assert.deepEqual(rows.map((row) => row.aweme_id), ['3', '2', '1']);
  assert.equal(cursorReinitialized, false);
});

test('includes the stop item and does not request older pages', async () => {
  const requested = [];
  const { videos: rows, cursorReinitialized } = await collectDouyinProfileVideos({
    fetchPage: async (cursor) => {
      requested.push(cursor);
      return {
        aweme_list: [video(5), video(4), video(3)],
        has_more: 1,
        max_cursor: 10,
      };
    },
    stopAwemeId: '4',
  });
  assert.deepEqual(rows.map((row) => row.aweme_id), ['5', '4']);
  assert.equal(cursorReinitialized, false);
  assert.deepEqual(requested, ['0']);
});

test('fails closed when the pagination cursor stalls', async () => {
  await assert.rejects(
    collectDouyinProfileVideos({
      fetchPage: async () => ({
        aweme_list: [video(1)],
        has_more: 1,
        max_cursor: 0,
      }),
    }),
    /cursor did not advance/,
  );
});

test('stops at either the second or fifth ID in a stop set', async () => {
  for (const position of [1, 4]) {
    const stopIds = ['90', '80', '70', '60', '50'];
    const { videos, cursorReinitialized } = await collectDouyinProfileVideos({
      fetchPage: async () => ({ aweme_list: [video(100), video(stopIds[position])], has_more: 0 }),
      stopAwemeId: stopIds.join(','),
    });
    assert.deepEqual(videos.map((row) => row.aweme_id), ['100', stopIds[position]]);
    assert.equal(cursorReinitialized, false);
  }
});

test('missing stops return only the newest bounded batch with a marker', async () => {
  const pages = {
    0: { aweme_list: Array.from({ length: 20 }, (_, index) => video(100 - index)), has_more: 1, max_cursor: 10 },
    10: { aweme_list: [video(80), video(79)], has_more: 0 },
  };
  const result = await collectDouyinProfileVideos({
    fetchPage: async (cursor) => pages[cursor],
    stopAwemeId: '999,998',
  });
  assert.equal(result.cursorReinitialized, true);
  assert.equal(result.videos.length, DEFAULT_CURSOR_REINITIALIZATION_LIMIT);
  assert.equal(result.videos[0].aweme_id, '100');
  const configured = await collectDouyinProfileVideos({
    fetchPage: async (cursor) => pages[cursor],
    stopAwemeId: '999',
    limit: 3,
  });
  assert.deepEqual(configured.videos.map((row) => row.aweme_id), ['100', '99', '98']);
});

test('missing stops at the page safety limit reinitialize only incremental collection', async () => {
  const requested = [];
  const fetchPage = async (cursor) => {
    requested.push(cursor);
    return {
      aweme_list: [video(100 - requested.length * 2), video(99 - requested.length * 2)],
      has_more: 1,
      max_cursor: Number(cursor) + 10,
    };
  };
  const incremental = await collectDouyinProfileVideos({
    fetchPage,
    maxPages: 2,
    limit: 2,
    stopAwemeId: '999,998',
  });
  assert.deepEqual(requested, ['0', '10']);
  assert.deepEqual(incremental.videos.map((row) => row.aweme_id), ['98', '97']);
  assert.equal(incremental.cursorReinitialized, true);

  await assert.rejects(
    collectDouyinProfileVideos({ fetchPage, maxPages: 2 }),
    /exceeded the safety limit of 2 pages/,
  );
});

test('normalizes unlimited and safety-bound inputs', () => {
  assert.equal(normalizeProfileLimit(0), 0);
  assert.equal(normalizeProfileLimit(-1), 0);
  assert.equal(normalizeProfileLimit(99), 99);
  assert.equal(normalizeMaxProfilePages(0), 1);
  assert.equal(normalizeMaxProfilePages(99_999), 2_000);
});

test('one to three known pinned videos never stop incremental collection', async () => {
  for (const count of [1, 2, 3]) {
    const pinned = Array.from({ length: count }, (_, index) => ({ ...video(index + 1), is_top: 1 }));
    const result = await collectDouyinProfileVideos({
      fetchPage: async () => ({
        aweme_list: [...pinned, video(100), video(99), video(98), video(97)], has_more: 0,
      }),
      stopAwemeId: [...pinned.map((item) => item.aweme_id), '98'].join(','),
    });
    assert.deepEqual(result.videos.map((item) => item.aweme_id), [
      ...pinned.map((item) => item.aweme_id), '100', '99', '98',
    ]);
    assert.deepEqual(result.videos.map((item) => item.is_top), [
      ...pinned.map(() => true), false, false, false,
    ]);
    assert.equal(result.cursorReinitialized, false);
  }
});

test('fallback detects older videos in the first three positions without is_top', async () => {
  const items = [1, 2, 3, 100, 99, 98].map((id) => ({ ...video(id), create_time: id }));
  const result = await collectDouyinProfileVideos({
    fetchPage: async () => ({ aweme_list: items, has_more: 0 }),
    stopAwemeId: '1,2,3,99',
  });
  assert.deepEqual(result.videos.map((item) => item.aweme_id), ['1', '2', '3', '100', '99']);
  assert.deepEqual(result.videos.map((item) => item.is_top), [true, true, true, false, false]);
  assert.equal(result.cursorReinitialized, false);
});

test('strictly descending timestamps without is_top still stop at the first known item', async () => {
  const items = [100, 99, 98].map((id) => ({ ...video(id), create_time: id }));
  const result = await collectDouyinProfileVideos({
    fetchPage: async () => ({ aweme_list: items, has_more: 0 }), stopAwemeId: '100',
  });
  assert.deepEqual(result.videos.map((item) => item.aweme_id), ['100']);
  assert.equal(result.videos[0].is_top, false);
  assert.equal(result.cursorReinitialized, false);
});

test('a known pin that is also newest stays marked known and scanning reaches the regular stop', async () => {
  const result = await collectDouyinProfileVideos({
    fetchPage: async () => ({
      aweme_list: [
        { ...video(100), is_top: true, create_time: 100 },
        { ...video(99), create_time: 99 },
        { ...video(98), create_time: 98 },
      ], has_more: 0,
    }), stopAwemeId: '100,99',
  });
  assert.deepEqual(result.videos.map((item) => [item.aweme_id, item.is_top]), [['100', true], ['99', false]]);
  assert.equal(result.cursorReinitialized, false);
});

test('a stop set containing only pins self-heals at the last page', async () => {
  const requested = [];
  const pages = {
    0: { aweme_list: [{ ...video(1), is_top: 1 }, video(100)], has_more: 1, max_cursor: 10 },
    10: { aweme_list: [video(99), video(98)], has_more: 0 },
  };
  const result = await collectDouyinProfileVideos({
    fetchPage: async (cursor) => { requested.push(cursor); return pages[cursor]; },
    stopAwemeId: '1', limit: 3,
  });
  assert.deepEqual(requested, ['0', '10']);
  assert.deepEqual(result.videos.map((item) => item.aweme_id), ['1', '100', '99']);
  assert.equal(result.cursorReinitialized, true);
});

test('initial collection keeps its limit including pins', async () => {
  const result = await collectDouyinProfileVideos({
    fetchPage: async () => ({
      aweme_list: [{ ...video(1), is_top: 1 }, video(100), video(99)], has_more: 1, max_cursor: 10,
    }), limit: 2,
  });
  assert.deepEqual(result.videos.map((item) => item.aweme_id), ['1', '100']);
  assert.equal(result.cursorReinitialized, false);
});

test('pin detection is pure and the fallback requires valid times and first-page positions', () => {
  for (const flag of [true, 1, '1']) {
    assert.equal(isPinnedProfileVideo({ is_top: flag }, 5, [], 2), true);
  }
  const later = { create_time: 100 };
  for (const invalid of [undefined, null, '', 'invalid', 0, -1, Infinity, true, []]) {
    assert.equal(isPinnedProfileVideo({ create_time: invalid }, 0, [later], 1), false);
    assert.equal(isPinnedProfileVideo({ create_time: 1 }, 0, [{}, { create_time: invalid }], 1), false);
  }
  const items = [{ create_time: 1 }, later];
  assert.equal(isPinnedProfileVideo(items[0], 0, items, 1), true);
  assert.equal(isPinnedProfileVideo(items[0], 0, items, 2), false);
  assert.equal(isPinnedProfileVideo({ create_time: 1 }, 3, [{}, {}, {}, {}, later], 1), false);
  assert.deepEqual(items, [{ create_time: 1 }, { create_time: 100 }]);
});
