import "../../styles/_card-reader.css";
import "../../styles/_browse-reader.css";
import { useEffect, useState } from "react";
import { fetchCardPreviewFacets, fetchCardPreviewPage } from "../../lib/api/cardPreview";
import type { CardPreviewFacets, CardPreviewListItem } from "../../lib/types/cardPreview";
import { BrowseCard } from "./BrowseCard";
import { BrowseFilters } from "./BrowseFilters";
import { browseUrl, readBrowseFilters, type BrowseFilters as Filters } from "./browseQueryState";
import { ReaderChrome } from "./ReaderChrome";
import { CardReaderModal, type CardReaderTarget } from "./CardReaderModal";

const PAGE_SIZE = 20;

export function BrowseReaderPage() {
  const [readerTarget, setReaderTarget] = useState<CardReaderTarget | null>(null);
  const [filters, setFilters] = useState(readBrowseFilters);
  const [facets, setFacets] = useState<CardPreviewFacets | null>(null);
  const [facetError, setFacetError] = useState(false);
  const [items, setItems] = useState<CardPreviewListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [nextOffset, setNextOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);

  function reset(next: Filters) {
    setFilters(next); setOffset(0); setNextOffset(0); setItems([]); setTotal(0); setLoading(true); setError(false);
  }
  function changeFilters(next: Filters) {
    window.history.pushState({}, "", browseUrl(next));
    reset(next);
  }
  useEffect(() => {
    const sync = () => reset(readBrowseFilters());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void fetchCardPreviewFacets(controller.signal, { scope: "maintained" }).then(
      value => { if (!controller.signal.aborted) { setFacets(value); setFacetError(false); } },
      () => { if (!controller.signal.aborted) setFacetError(true); },
    );
    return () => controller.abort();
  }, [retry]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false);
    void fetchCardPreviewPage({ ...filters, limit: PAGE_SIZE, offset }, controller.signal).then(
      page => {
        if (controller.signal.aborted) return;
        setItems(previous => offset === 0 ? page.items : [...new Map([...previous, ...page.items].map(item => [item.card_id, item])).values()]);
        setTotal(page.total); setNextOffset(offset + page.items.length); setLoading(false);
      },
      () => { if (!controller.signal.aborted) { setError(true); setLoading(false); } },
    );
    return () => controller.abort();
  }, [filters, offset, retry]);

  return <ReaderChrome active="browse" wide><div className="browse-reader">
    <header className="card-reader__header browse-reader__hero"><p className="card-reader__eyebrow">Random walk · 已评估的信息</p><h1>漫步</h1>
      <p className="card-reader__meta">看看关注的信息源最近带来了什么，以及 agent 为什么觉得它值得一看。</p></header>
    <BrowseFilters filters={filters} facets={facets} onChange={changeFilters} />
    {facetError ? <p role="alert">筛选选项暂时无法读取。<button onClick={() => setRetry(value => value + 1)}>重试选项</button></p> : null}
    {facets?.truncated.sources || facets?.truncated.platforms ? <p role="status">筛选选项较多，仅显示卡数最多的平台和来源。</p> : null}
    {filters.evaluation === "evaluated" ? <p className="card-reader__state">未过线卡置后，各组内按发布时间倒序。</p> : null}
    <section className="browse-reader__grid" aria-label="信息卡列表" aria-busy={loading}>
      {items.map(item => <BrowseCard key={item.card_id} item={item} onOpen={cardId => setReaderTarget({ cardId })} />)}
    </section>
    {loading ? <p className="browse-reader__loading" role="status"><span className="spinner" aria-hidden="true" />正在加载信息卡…</p> : null}
    {error ? <p role="alert">信息卡暂时无法加载。<button onClick={() => setRetry(value => value + 1)}>重试</button></p> : null}
    {!loading && !error && !items.length ? <p role="status">没有符合筛选条件的信息卡。</p> : null}
    {!error && nextOffset < total ? <button className="browse-reader__more" disabled={loading} onClick={() => setOffset(nextOffset)}>加载更多</button> : null}
    {!loading && !error && items.length > 0 ? <p className="card-reader__state">已显示 {items.length} / {total} 条{nextOffset >= total ? " · 已到底" : ""}</p> : null}
  </div><CardReaderModal target={readerTarget} onClose={() => setReaderTarget(null)} /></ReaderChrome>;
}
