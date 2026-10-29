// Shared DOM filter driver. No OpenCLI imports: errors are translated by adapters.
export class SearchFilterError extends Error {
  constructor(step, message, auth = false) {
    super(`${step}: ${message}`);
    this.auth = auth;
  }
}

export function normalizeFilters(kwargs, sortLabels, publishLabels) {
  const options = { sort: kwargs.sort ?? 'general', publish_time: kwargs['publish-time'] ?? 'any' };
  for (const [key, labels] of [['sort', sortLabels], ['publish_time', publishLabels]]) {
    if (!Object.hasOwn(labels, options[key])) {
      throw new TypeError(`--${key.replace('_', '-')} must be one of ${Object.keys(labels).join('|')}`);
    }
  }
  return options;
}

export function filterPlan(options, sortLabels, publishLabels) {
  return [
    ...(options.sort === 'general' ? [] : [{ group: '排序依据', label: sortLabels[options.sort], groupIndex: '0', optionIndex: String(Object.keys(sortLabels).indexOf(options.sort)) }]),
    ...(options.publish_time === 'any' ? [] : [{ group: '发布时间', label: publishLabels[options.publish_time], groupIndex: '1', optionIndex: String(Object.keys(publishLabels).indexOf(options.publish_time)) }]),
  ];
}

function unwrap(value) {
  return value && typeof value === 'object' && 'session' in value && 'data' in value ? value.data : value;
}

// This function runs in page context; keep every dependency inside it.
function inspect(config, action, option) {
  const text = (el) => (el?.innerText || '').replace(/\s/g, '');
  const visible = (el) => {
    if (!el || el.closest('[aria-hidden="true"]')) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none'
      && style.visibility !== 'hidden' && Number(style.opacity) > 0.01;
  };
  const bodyText = document.body?.innerText || '';
  const rows = () => [...document.querySelectorAll(config.rows)];
  const wall = config.site === 'xiaohongshu'
    ? /登录后查看搜索结果|验证码|安全校验/.test(bodyText)
    : /登录后查看|请先登录|登录抖音|验证码|验证|verify_check|安全校验/.test(bodyText);
  const modal = document.querySelector('[class*="login-mask"], [class*="LoginMask"], [class*="login-modal"], dialog[role="dialog"]');
  if ((wall && !rows().length) || visible(modal)) return { auth: true };
  const signature = () => rows().map(el => el.innerText + [...el.querySelectorAll('a')].map(a => a.href).join('|')).join('\n');
  // Count by start time, not by total: Douyin clears the resource-timing buffer between searches.
  const requests = (since = -1) => performance.getEntriesByType('resource')
    .filter(entry => entry.name.includes(config.request) && (entry.startTime ?? 0) > since).length;
  if (action === 'rows') return { found: rows().length > 0 };
  if (action === 'entry') {
    document.querySelectorAll('[data-infoos-filter-entry]').forEach(el => el.removeAttribute('data-infoos-filter-entry'));
    const candidates = [...document.querySelectorAll(config.site === 'douyin' ? 'span' : '*')];
    // Deepest match: outer wrappers share the same text but hovering their centre misses the trigger.
    const entry = candidates.filter(el => text(el) === '筛选' && el.children.length <= 3 && visible(el)).at(-1);
    const target = config.site === 'xiaohongshu' ? entry?.parentElement : entry;
    if (!visible(target)) return { found: false };
    target.setAttribute('data-infoos-filter-entry', 'true');
    document.querySelectorAll('[data-infoos-filter-away]').forEach(el => el.removeAttribute('data-infoos-filter-away'));
    const away = rows().find(visible);
    if (away) away.setAttribute('data-infoos-filter-away', 'true');
    return { found: true, away: Boolean(away) };
  }
  const group = config.site === 'xiaohongshu'
    ? [...document.querySelectorAll('.filter-container .filters')].find(el => [...el.children].some(child => text(child) === option.group))
    : [...document.querySelectorAll('div')].find(el => [...el.children].some(child => text(child) === option.group)
      && el.querySelector('span[data-index1]'));
  if (action === 'panel') return { found: visible(group) };
  if (action === 'option') {
    document.querySelectorAll('[data-infoos-filter-option]').forEach(el => el.removeAttribute('data-infoos-filter-option'));
    if (!visible(group)) return { found: false };
    const candidates = [...group.querySelectorAll(config.site === 'xiaohongshu' ? '.tags' : 'span[data-index1]')].filter(visible);
    const target = candidates.find(el => text(el) === option.label && (config.site !== 'douyin'
      || (el.getAttribute('data-index1') === option.groupIndex && el.getAttribute('data-index2') === option.optionIndex)));
    if (!target) return { found: false };
    // Douyin selected options have an extra class; never depend on obfuscated names.
    const selected = config.site === 'xiaohongshu' ? target.classList.contains('active')
      : candidates.length > 1 && target.classList.length > Math.min(...candidates.map(el => el.classList.length));
    target.setAttribute('data-infoos-filter-option', 'true');
    return { found: true, selected };
  }
  if (action === 'before') {
    // A full resource-timing buffer silently drops the refresh request we wait for.
    try { performance.setResourceTimingBufferSize(3000); } catch { /* keep the default buffer */ }
    window.__infoosSearchFilter = { mark: performance.now(), rows: rows(), signature: signature(), lastSignature: signature(), stableSince: Date.now(), newRequestAt: null };
    return { found: true };
  }
  if (action === 'after') {
    const state = window.__infoosSearchFilter;
    // A filter click can hard-navigate (seen on Douyin for queries with spaces): the marker is gone.
    if (!state) return { found: false, navigated: true };
    const current = rows();
    const currentSignature = signature();
    const newRequest = requests(state.mark) > 0;
    if (newRequest && state.newRequestAt === null) state.newRequestAt = Date.now();
    if (currentSignature !== state.lastSignature) {
      state.lastSignature = currentSignature;
      state.stableSince = Date.now();
    }
    const rerendered = currentSignature !== state.signature || current.some((el, i) => el !== state.rows[i]);
    // Give the newly completed resource time to commit its DOM update even if rows are identical.
    const stable = newRequest && Date.now() - Math.max(state.stableSince, state.newRequestAt) >= 750;
    return { found: newRequest && (rerendered || stable), newRequest };
  }
  return { found: false };
}

export async function applyFilters(page, config, plan) {
  if (!plan.length) return;
  const check = async (action, option = plan[0]) => {
    const result = unwrap(await page.evaluate(`(${inspect.toString()})(${JSON.stringify(config)},${JSON.stringify(action)},${JSON.stringify(option)})`));
    // While a navigation is settling the body can briefly match the wall text; 'rows' only waits.
    if (result?.auth && action !== 'rows') throw new SearchFilterError(action, 'login/security wall', true);
    return result;
  };
  const pause = () => page.evaluate('new Promise(resolve => setTimeout(resolve, 200))');
  const poll = async (action, option, timeout) => {
    const deadline = Date.now() + timeout;
    do {
      const result = await check(action, option);
      if (result?.found) return result;
      await pause();
    } while (Date.now() < deadline);
    return null;
  };
  // Returns 'refreshed', 'navigated' or null (timeout). Evaluate rejects while the old document detaches.
  const pollRefresh = async (option, timeout) => {
    const deadline = Date.now() + timeout;
    do {
      let result;
      try {
        result = await check('after', option);
      } catch (error) {
        if (error instanceof SearchFilterError) throw error;
        result = { navigated: true };
      }
      if (result?.found) return 'refreshed';
      if (result?.navigated) return 'navigated';
      try { await pause(); } catch { return 'navigated'; }
    } while (Date.now() < deadline);
    return null;
  };
  const settleAfterNavigation = async () => {
    const deadline = Date.now() + 15000;
    do {
      try {
        if ((await check('rows'))?.found) return true;
      } catch (error) {
        if (error instanceof SearchFilterError) throw error;
      }
      await new Promise(resolve => setTimeout(resolve, 300));
    } while (Date.now() < deadline);
    return false;
  };
  let step = 'hover entry';
  try {
    if (typeof page.hover !== 'function') throw new SearchFilterError(step, 'page.hover is unavailable');
    const openPanel = async (option) => {
      step = 'locate filter entry';
      const entry = await check('entry', option);
      if (!entry?.found) throw new SearchFilterError(step, '筛选 entry not found');
      step = 'hover filter entry';
      await page.hover('[data-infoos-filter-entry="true"]');
      step = `wait filter panel (${option.group})`;
      if (await poll('panel', option, 3000)) return;
      // The first hover is sometimes swallowed (seen on Douyin); a second mouseenter needs the pointer to leave first.
      if (entry.away) await page.hover('[data-infoos-filter-away="true"]');
      await page.hover('[data-infoos-filter-entry="true"]');
      if (!await poll('panel', option, 3000)) throw new SearchFilterError(step, 'panel did not appear within 6s');
    };
    await openPanel(plan[0]);
    for (const option of plan) {
      if (!(await check('panel', option))?.found) await openPanel(option);
      step = `locate option (${option.group}: ${option.label})`;
      const target = await check('option', option);
      if (!target?.found) throw new SearchFilterError(step, 'visible option not found');
      if (target.selected) continue;
      const timeoutMessage = 'no new search request and updated/stable list within 10s';
      for (let attempt = 0; ; attempt += 1) {
        await check('before', option);
        step = `click option (${option.group}: ${option.label})`;
        await page.click('[data-infoos-filter-option="true"]');
        step = `wait search refresh (${option.group}: ${option.label})`;
        const outcome = await pollRefresh(option, 10000);
        if (outcome === 'refreshed') break;
        if (outcome !== 'navigated') throw new SearchFilterError(step, timeoutMessage);
        step = `wait results after navigation (${option.group}: ${option.label})`;
        if (!await settleAfterNavigation()) {
          await check('entry', option);
          throw new SearchFilterError(step, 'results did not render within 15s');
        }
        // Trust the reloaded page only if it shows the option as selected; otherwise click once more.
        step = `verify option after navigation (${option.group}: ${option.label})`;
        await openPanel(option);
        const reloaded = await check('option', option);
        if (!reloaded?.found) throw new SearchFilterError(step, 'visible option not found');
        if (reloaded.selected) break;
        if (attempt >= 1) throw new SearchFilterError(step, 'filter was not kept after page navigation');
      }
    }
  } catch (error) {
    if (error instanceof SearchFilterError) throw error;
    throw new SearchFilterError(step, error instanceof Error ? error.message : String(error));
  }
}
