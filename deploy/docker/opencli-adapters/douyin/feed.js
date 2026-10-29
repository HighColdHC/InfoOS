import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import { bounded, evaluate, WEB } from './_social.js';

import { slideDataRow } from './feed-row.js';
import { prefilterOptions, prefilterRow } from './feed-prefilter.js';
import { collectFeed, dwellOptions, feedReadError, FeedReadError, openWithDetachedRetry } from './feed-session.js';

const READ_VISIBLE_ITEM = (withPrefilter = false) => `(() => {
  const list = document.querySelector('#slidelist.recommend-slidelist');
  if (!list) return { error: 'recommend_slide_list_missing', url: location.href };
  const area = list.getBoundingClientRect();
  const middle = area.top + area.height / 2;
  const slide = [...list.querySelectorAll('[data-e2e="feed-item"]')]
    .find(element => {
      const rect = element.getBoundingClientRect();
      return rect.top <= middle && rect.bottom > middle;
    });
  if (!slide) return { error: 'visible_slide_missing', url: location.href };
  const fiberKey = Object.keys(slide).find(key => key.startsWith('__reactFiber'));
  let fiber = fiberKey ? slide[fiberKey] : null;
  let props = null;
  for (let depth = 0; depth < 6 && fiber; depth += 1, fiber = fiber.return) {
    if (fiber.memoizedProps?.slideData) {
      props = fiber.memoizedProps;
      break;
    }
  }
  const item = props?.slideData;
  if (!item || !Number.isInteger(props.currentIndex)) {
    return { error: 'recommend_slide_data_missing', url: location.href };
  }
  return {
    url: location.href,
    position: props.currentIndex + 1,
    wheel: { x: Math.round(area.left + area.width / 2), y: Math.round(middle) },
    item: (${slideDataRow.toString()})(item, ${withPrefilter}),
  };
})()`;

async function recommendReady(page) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const state = await evaluate(page, `(() => ({
      url: location.href,
      slides: document.querySelectorAll('#slidelist.recommend-slidelist [data-e2e="feed-item"]').length,
    }))()`);
    const url = new URL(state.url);
    if (url.hostname === 'www.douyin.com' && url.pathname === '/'
      && url.searchParams.get('recommend') === '1' && state.slides > 0) return;
    await page.wait({ time: 1 });
  }
  throw new CommandExecutionError('Douyin did not open the web recommendation feed');
}

async function openRecommend(page) {
  await page.goto(`${WEB}/jingxuan`);
  await page.wait({ time: 2 });
  const link = await evaluate(page, `(() => !!document.querySelector('a[href*="recommend=1"]'))()`);
  if (!link) throw new CommandExecutionError('Douyin recommendation navigation is unavailable');
  await page.click('a[href*="recommend=1"]');
  await recommendReady(page);
  const guide = await evaluate(page, `(() => {
    const button = document.querySelector('#douyin-web-recommend-guide-mask button');
    return !!button?.getClientRects().length;
  })()`);
  if (guide) await page.click('#douyin-web-recommend-guide-mask button');
}

async function visibleItem(page, withPrefilter = false) {
  let state;
  try { state = await evaluate(page, READ_VISIBLE_ITEM(withPrefilter)); }
  catch (error) { throw feedReadError(error, 'item_unreadable'); }
  if (state.error || (!state.item?.is_live && !/^\d{15,22}$/.test(state.item?.aweme_id || ''))) {
    throw new FeedReadError('Douyin web feed item unavailable', { code: 'item_unreadable' });
  }
  return state;
}

async function nextItem(page, previous, withPrefilter = false) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await page.cdp('Input.dispatchMouseEvent', {
      type: 'mouseWheel', x: previous.wheel.x, y: previous.wheel.y,
      deltaX: 0, deltaY: 760,
    });
    for (let wait = 0; wait < 3; wait += 1) {
      await page.wait({ time: 1 });
      const current = await visibleItem(page, withPrefilter);
      if (current.position === previous.position + 1) return current;
      if (current.position > previous.position + 1) {
        throw new FeedReadError('Douyin feed skipped next position', { code: 'skipped_position' });
      }
    }
  }
  throw new FeedReadError('Douyin feed stopped advancing', { code: 'no_progress' });
}

cli({
  site: 'douyin', name: 'feed', access: 'read',
  description: '在抖音网页版推荐页逐条下刷，读取视频、直播和信息流广告',
  domain: 'www.douyin.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [
    { name: 'limit', type: 'int', default: 10, help: '按实际推荐顺序读取 1-50 条' },
    { name: 'dwell_min', type: 'int', default: 0, help: '每条最短停留秒数（0-60）' },
    { name: 'dwell_max', type: 'int', default: 0, help: '每条最长停留秒数（0-60）' },
    { name: 'with_meta', type: 'bool', default: false, help: '返回条目和批次完整性信息（配合 -f json）' },
    { name: 'prefilter', type: 'string', help: '后端投影的变现初筛规则 JSON；省略时关闭' },
  ],
  columns: ['index', ...Object.keys(slideDataRow()), 'feed_url'],
  func: async (page, kwargs) => {
    const limit = bounded(kwargs.limit, 'limit', 10, 50);
    let dwellMin, dwellMax;
    try { [dwellMin, dwellMax] = dwellOptions(kwargs.dwell_min, kwargs.dwell_max); }
    catch (error) { throw new ArgumentError(error.message); }
    let prefilter;
    try { prefilter = prefilterOptions(kwargs.prefilter); }
    catch (error) { throw new ArgumentError(error.message); }
    await openWithDetachedRetry(() => openRecommend(page));
    return collectFeed({
      limit, dwellMin, dwellMax,
      withMeta: kwargs.with_meta === true || kwargs.with_meta === 'true',
      read: () => visibleItem(page, !!prefilter),
      advance: previous => nextItem(page, previous, !!prefilter),
      prefilter: prefilter ? row => prefilterRow(page, row, prefilter, evaluate) : null,
      wait: time => page.wait({ time }),
    });
  },
});
