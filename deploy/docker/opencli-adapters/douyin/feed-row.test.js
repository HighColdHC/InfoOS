import assert from 'node:assert/strict';
import test from 'node:test';
import { slideDataRow } from './feed-row.js';
import { collectFeed, dwellOptions, FeedReadError, openWithDetachedRetry } from './feed-session.js';

test('ordinary video projects authors, metrics and millisecond duration', () => {
  const row = slideDataRow({ awemeId: '9000000000000000007', itemTitle: '视频', createTime: 123,
    authorInfo: { nickname: '作者', shortId: '1234', secUid: 'MS4w' },
    stats: { diggCount: 10, commentCount: 2, shareCount: 3, collectCount: 4 },
    video: { duration: 12999, cover: [{ src: '//cdn.example/cover.jpg' }], playApi: 'https://cdn.example/play' },
  });
  assert.equal(row.author_handle, '1234');
  assert.equal(row.duration, 12);
  assert.equal(row.share_count, 3);
  assert.equal(row.collect_count, 4);
  assert.equal(row.cover_url, 'https://cdn.example/cover.jpg');
  assert.equal(row.is_ad, false);
  assert.equal(row.url, 'https://www.douyin.com/video/9000000000000000007');
  assert.equal(row.ad_id, '');
  assert.equal(row.ad_is_soft, false);
  assert.ok(Object.values(row).every(value => value !== undefined));
  assert.equal(slideDataRow({ authorInfo: { uniqueId: 'handle', shortId: '123' } }).author_handle, 'handle');
});

test('DOU+ parses double JSON while preserving unquoted large IDs exactly', () => {
  const raw = '{"ad_id":12345678901234567890,"creative_id":90000000000000000001,'
    + '"title":"广告标题","button_text":"查看详情","open_url":"https://live.douyin.com/1",'
    + '"web_url":"","group_type":2,"ad_source_type":3,"log_extra":'
    + JSON.stringify('{"predict_identity":"aweme_dou_plus","is_soft_ad":1,"soft_ad_ascription_advertiser_id":12345678901234567891,"room_id":"12345"}') + '}';
  const row = slideDataRow({ itemTitle: '视频标题', rawAdData: raw });
  assert.equal(row.is_ad, true);
  assert.equal(row.ad_id, '12345678901234567890');
  assert.equal(row.ad_creative_id, '90000000000000000001');
  assert.equal(row.ad_advertiser_id, '12345678901234567891');
  assert.equal(row.ad_live_room_id, '12345');
  assert.equal(row.ad_kind, 'aweme_dou_plus');
  assert.equal(row.ad_is_soft, true);
  assert.equal(row.ad_title, '广告标题');
  assert.equal(row.title, '视频标题');
  assert.equal(row.ad_button_text, '查看详情');
  assert.equal(row.ad_web_url, '');
  assert.equal(row.ad_group_type, 2);
  assert.equal(row.ad_source_type, 3);
  assert.equal(row.ad_landing_url, 'https://live.douyin.com/1');
});

test('live rows have no video URL or playable candidates', () => {
  const row = slideDataRow({ awemeType: 101, video: { playApi: '//cdn.example/live' } });
  assert.equal(row.is_live, true);
  assert.equal(row.title, '直播中');
  assert.equal(row.url, '');
  assert.equal(row.play_url, '');
  assert.deepEqual(row.play_urls, []);
});

test('invalid advertisement JSON and missing log fields use safe defaults', () => {
  for (const rawAdData of ['{invalid', 'null', '[]', '{"log_extra":"invalid"}']) {
    const row = slideDataRow({ rawAdData });
    assert.equal(row.is_ad, true);
    assert.equal(row.ad_id, '');
    assert.equal(row.ad_kind, '');
    assert.equal(row.ad_is_soft, false);
    assert.equal(row.ad_advertiser_id, '');
  }
});

test('play candidates normalize protocol and deduplicate in declared order', () => {
  const row = slideDataRow({ video: { playApi: '//cdn.example/api',
    playAddr: [{ src: '//cdn.example/first' }, { src: 'https://cdn.example/api' }, { src: '//cdn.example/second' }],
    playAddrH265: [{ src: '//cdn.example/hevc' }, { src: '//cdn.example/first' }],
  } });
  assert.deepEqual(row.play_urls, ['https://cdn.example/api', 'https://cdn.example/first',
    'https://cdn.example/second', 'https://cdn.example/hevc']);
  assert.equal(row.play_url, row.play_urls[0]);
  assert.equal(slideDataRow({ video: { playAddr: [{ src: '//cdn.example/fallback' }] } }).play_url,
    'https://cdn.example/fallback');
});

test('opening retries detached errors exactly once and no other failures', async () => {
  let calls = 0;
  await openWithDetachedRetry(async () => { if (++calls === 1) throw Error('Detached while handling command.'); });
  assert.equal(calls, 2);
  calls = 0;
  await assert.rejects(openWithDetachedRetry(async () => { calls++; throw Error('Detached while handling command.'); }));
  assert.equal(calls, 2);
  calls = 0;
  await assert.rejects(openWithDetachedRetry(async () => { calls++; throw Error('unavailable'); }));
  assert.equal(calls, 1);
});

test('partial rows stop on advancement/unreadable errors; first read still fails', async () => {
  for (const message of ['stopped advancing', 'skipped from item', 'item unavailable']) {
    let calls = 0;
    const rows = await collectFeed({ limit: 3,
      read: async () => ({ position: 1, item: slideDataRow() }),
      advance: async () => { calls++; throw new FeedReadError(message); }, wait: async () => {},
    });
    assert.equal(rows.length, 1);
    assert.equal(calls, 1);
    assert.equal(rows[0].index, 1);
    assert.ok(!('error' in rows[0]));
  }
  await assert.rejects(collectFeed({ limit: 3, read: async () => { throw new FeedReadError('unavailable'); } }));
});

test('dwell ranges add ad delay only when enabled and never wait after last row', async () => {
  for (const [min, max, random, expected] of [[0, 0, 0, []], [6, 20, 0, [9]], [6, 20, .999, [28]]]) {
    const waits = [];
    const rows = await collectFeed({ limit: 2, dwellMin: min, dwellMax: max, random: () => random,
      read: async () => ({ position: 1, item: { is_ad: true } }),
      advance: async () => ({ position: 2, item: { is_ad: false } }),
      wait: async seconds => waits.push(seconds),
    });
    assert.equal(rows.length, 2);
    assert.deepEqual(waits, expected);
  }
  for (const values of [[-1, 1], [0, 61], [3, 2], [1.5, 2]]) assert.throws(() => dwellOptions(...values));
  assert.deepEqual(dwellOptions(), [0, 0]);
});

test('ad without a caption takes the ad copy as its title; a caption still wins', () => {
  const raw = JSON.stringify({ title: '广告文案' });
  assert.equal(slideDataRow({ awemeId: '9000000000000000007', rawAdData: raw }).title, '广告文案');
  assert.equal(slideDataRow({ itemTitle: '视频标题', rawAdData: raw }).title, '视频标题');
});
