import { useEffect, useState } from "react";
import type { CardPreviewFacets } from "../../lib/types/cardPreview";
import type { BrowseFilters as Filters } from "./browseQueryState";

export function BrowseFilters({ filters, facets, onChange }: {
  filters: Filters; facets: CardPreviewFacets | null; onChange: (filters: Filters) => void;
}) {
  const [query, setQuery] = useState(filters.query);
  useEffect(() => setQuery(filters.query), [filters.query]);
  const sources = facets?.sources.filter(source => source.platform === filters.platform) ?? [];
  return <form className="browse-reader__filters" aria-label="漫步筛选" onSubmit={event => {
    event.preventDefault(); onChange({ ...filters, query: query.trim() });
  }}>
    <label>平台<select value={filters.platform} onChange={event => onChange({ ...filters, platform: event.target.value, source_id: "" })}>
      <option value="">全部平台</option>
      {filters.platform && !facets?.platforms.some(p => p.platform === filters.platform) ? <option value={filters.platform}>{filters.platform}</option> : null}
      {facets?.platforms.map(platform => <option key={platform.platform} value={platform.platform}>{platform.label}（{platform.card_count}）</option>)}
    </select></label>
    <label>来源<select value={filters.source_id} disabled={!filters.platform} onChange={event => onChange({ ...filters, source_id: event.target.value })}>
      <option value="">{filters.platform ? "全部来源" : "先选择平台"}</option>
      {filters.source_id && !sources.some(s => s.source_id === filters.source_id) ? <option value={filters.source_id}>当前来源（{filters.source_id}）</option> : null}
      {sources.map(source => <option key={source.source_id} value={source.source_id}>{source.name}（{source.card_count}）</option>)}
    </select></label>
    <label>视图<select value={filters.evaluation} onChange={event => {
      const evaluation = event.target.value as Filters["evaluation"];
      onChange({ ...filters, evaluation, sort: evaluation === "passed" ? filters.sort : "published_desc" });
    }}>
      <option value="passed">已过线</option><option value="evaluated">全部已评估</option><option value="unevaluated">未评估</option>
    </select></label>
    <label>排序<select value={filters.sort} onChange={event => onChange({ ...filters, sort: event.target.value as Filters["sort"] })}>
      <option value="published_desc">发布时间倒序</option>
      {filters.evaluation === "passed" ? <option value="relevance_desc">相关度</option> : null}
    </select></label>
    <label className="browse-reader__search">关键词<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索标题、作者或正文" /></label>
    <button type="submit">搜索</button>
  </form>;
}
