import { normalizeFilters, filterPlan, applyFilters } from '../_infoos/search-filters.js';
export const SORT_LABELS = { general: '综合排序', latest: '最新发布', 'most-liked': '最多点赞' };
export const PUBLISH_LABELS = { any: '不限', day: '一天内', week: '一周内', 'half-year': '半年内' };
export const parseFilters = kwargs => normalizeFilters(kwargs, SORT_LABELS, PUBLISH_LABELS);
export const planFilters = options => filterPlan(options, SORT_LABELS, PUBLISH_LABELS);
export const applySearchFilters = (page, options) => applyFilters(page,
  { site: 'douyin', rows: '[data-e2e="scroll-list"] li', request: '/aweme/v1/web/search/item/' }, planFilters(options));
