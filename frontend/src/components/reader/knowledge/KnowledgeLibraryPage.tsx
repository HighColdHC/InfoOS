import "../../../styles/_card-reader.css";
import "../../../styles/_browse-reader.css";
import "../../../styles/_knowledge-library.css";
import { useEffect, useState } from "react";
import { fetchKnowledgeLibrary } from "../../../lib/api/knowledge";
import type { KnowledgeFacet, KnowledgeListItem } from "../../../lib/types/knowledge";
import { RESEARCH_LABELS } from "../../../lib/types/knowledge";
import { ReaderChrome } from "../ReaderChrome";
import { KnowledgeTile } from "./KnowledgeTile";
import { knowledgeUrl, readKnowledgeFilters, selectKnowledgeKind, type KnowledgeFilters } from "./knowledgeQueryState";

const PAGE_SIZE = 20;

export function KnowledgeLibraryPage() {
  const [filters, setFilters] = useState(readKnowledgeFilters);
  const [query, setQuery] = useState(filters.query);
  const [facets, setFacets] = useState<KnowledgeFacet[]>([]);
  const [items, setItems] = useState<KnowledgeListItem[]>([]);
  const [cursor, setCursor] = useState<string | undefined>();
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);

  function reset(next: KnowledgeFilters) {
    setFilters(next); setQuery(next.query); setCursor(undefined); setNextCursor(null); setItems([]); setLoading(true); setError(false);
  }
  function changeFilters(next: KnowledgeFilters) {
    window.history.pushState({}, "", knowledgeUrl(next));
    reset(next);
  }
  useEffect(() => {
    const sync = () => reset(readKnowledgeFilters());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false);
    void fetchKnowledgeLibrary({ ...filters, limit: PAGE_SIZE, cursor }, controller.signal).then(
      page => {
        if (controller.signal.aborted) return;
        setItems(previous => cursor === undefined ? page.items : [...new Map([...previous, ...page.items].map(item => [item.knowledge_id, item])).values()]);
        setFacets(page.facets); setNextCursor(page.next_cursor); setLoading(false);
      },
      () => { if (!controller.signal.aborted) { setError(true); setLoading(false); } },
    );
    return () => controller.abort();
  }, [filters, cursor, retry]);

  const selected = facets.find(facet => facet.kind === filters.kind);
  return <ReaderChrome active="knowledge" wide><div className="browse-reader knowledge-library">
    <header className="card-reader__header browse-reader__hero"><p className="card-reader__eyebrow">InfoOS · 知识库</p><h1>知识</h1>
      <p className="card-reader__meta">按用途浏览从信息卡中拆出的知识与原文证据，按更新时间倒序。</p></header>
    <div className="knowledge-library__tabs" role="group" aria-label="知识类型">
      <button type="button" aria-pressed={!filters.kind} onClick={() => changeFilters(selectKnowledgeKind(filters, ""))}>全部（{facets.reduce((sum, facet) => sum + facet.count, 0)}）</button>
      {facets.map(facet => <button type="button" key={facet.kind} aria-pressed={filters.kind === facet.kind}
        onClick={() => changeFilters(selectKnowledgeKind(filters, facet.kind))}>{facet.label}（{facet.count}）</button>)}
    </div>
    {selected ? <div className="knowledge-library__tabs" role="group" aria-label="知识子类型">
      <button type="button" aria-pressed={!filters.subtype} onClick={() => changeFilters({ ...filters, subtype: "" })}>全部（{selected.count}）</button>
      {selected.subtypes.map(subtype => <button type="button" key={subtype.subtype} aria-pressed={filters.subtype === subtype.subtype}
        onClick={() => changeFilters({ ...filters, subtype: subtype.subtype })}>{subtype.label}（{subtype.count}）</button>)}
    </div> : null}
    <form className="browse-reader__filters knowledge-library__filters" aria-label="知识筛选" onSubmit={event => {
      event.preventDefault(); changeFilters({ ...filters, query: query.trim() });
    }}>
      {filters.kind === "opportunity" ? <label>调研状态<select value={filters.research_status} onChange={event => changeFilters({ ...filters, research_status: event.target.value })}>
        <option value="">全部状态</option>
        {Object.entries(RESEARCH_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label> : null}
      <label className="browse-reader__search">关键词<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索标题、用处或字段，空格分词" /></label>
      <button type="submit">搜索</button>
    </form>
    <section className="browse-reader__grid" aria-label="知识卡列表" aria-busy={loading}>
      {items.map(item => <KnowledgeTile key={item.knowledge_id} item={item} />)}
    </section>
    {loading ? <p className="browse-reader__loading" role="status"><span className="spinner" aria-hidden="true" />正在加载知识卡…</p> : null}
    {error ? <p role="alert">知识卡暂时无法加载。<button onClick={() => setRetry(value => value + 1)}>重试</button></p> : null}
    {!loading && !error && !items.length ? <p role="status">没有符合筛选条件的知识卡。</p> : null}
    {!error && nextCursor !== null ? <button className="browse-reader__more" disabled={loading} onClick={() => setCursor(nextCursor)}>加载更多</button> : null}
    {!loading && !error && items.length > 0 ? <p className="card-reader__state">已显示 {items.length} 条{nextCursor === null ? " · 已到底" : ""}</p> : null}
  </div></ReaderChrome>;
}
