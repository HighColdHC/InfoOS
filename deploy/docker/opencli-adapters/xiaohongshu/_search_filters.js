import { normalizeFilters, filterPlan, applyFilters } from '../_infoos/search-filters.js';
export const SORT_LABELS = { general: '综合', latest: '最新', 'most-liked': '最多点赞', 'most-commented': '最多评论', 'most-collected': '最多收藏' };
export const PUBLISH_LABELS = { any: '不限', day: '一天内', week: '一周内', 'half-year': '半年内' };
export const parseFilters = kwargs => normalizeFilters(kwargs, SORT_LABELS, PUBLISH_LABELS);
export const planFilters = options => filterPlan(options, SORT_LABELS, PUBLISH_LABELS);
export const applySearchFilters = (page, options) => applyFilters(page,
  { site: 'xiaohongshu', rows: '.note-item', request: '/api/sns/web/v2/search/notes' }, planFilters(options));
