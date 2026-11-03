import "../../../styles/_card-reader.css";
import "../../../styles/_opportunities.css";
import { useEffect, useRef, useState } from "react";
import { fetchOpportunityBoard, fetchOpportunityStats, setResearchStatus } from "../../../lib/api/opportunities";
import type { BoardData, BoardFilters, OpportunityItem, ResearchStatus, StatsData } from "../../../lib/types/opportunities";
import { ReaderChrome } from "../ReaderChrome";
import { OpportunityTile } from "./OpportunityTile";
import { moveBoard } from "./boardState";
import { OpportunityHeader, SUBTYPES } from "./shared";

export function OpportunityBoardPage() {
  const legacyCard = new URLSearchParams(window.location.search).get("card");
  const [filters, setFilters] = useState<BoardFilters>({ query: "", platform: "", subtype: "" });
  const [data, setData] = useState<BoardData | null>(null);
  const [platforms, setPlatforms] = useState<StatsData["by_platform"]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const writing = useRef(false);
  const generation = useRef(0);
  const [dragId, setDragId] = useState<string | null>(null);
  const [over, setOver] = useState<ResearchStatus | null>(null);
  const [toast, setToast] = useState("");
  useEffect(() => {
    if (legacyCard) window.location.replace(`/r/card/${encodeURIComponent(legacyCard)}`);
  }, [legacyCard]);
  useEffect(() => {
    if (legacyCard) return;
    const controller = new AbortController();
    void Promise.all([fetchOpportunityBoard(filters, controller.signal), fetchOpportunityStats("all", controller.signal)])
      .then(([value, stats]) => { if (!controller.signal.aborted) { setData(value); setPlatforms(stats.by_platform); setLoading(false); setError(false); } },
        () => { if (!controller.signal.aborted) { setLoading(false); setError(true); } });
    return () => controller.abort();
  }, [filters, retry, legacyCard]);
  function change(next: BoardFilters) { generation.current++; setLoading(true); setError(false); setFilters(next); }
  async function move(item: OpportunityItem, target: ResearchStatus) {
    if (data === null || writing.current || loading || target === item.research_status) return;
    const before = data;
    writing.current = true; setBusy(true); setData(moveBoard(data, item, target)); setToast("");
    try {
      await setResearchStatus(item.knowledge_id, target, null);
      setToast(`已移到“${data.columns.find(column => column.status === target)!.label}”`);
    } catch {
      setData(before); setToast("调研状态保存失败，已恢复原状态，请重试");
      setBusy(false); writing.current = false; return;
    }
    try { setData(await fetchOpportunityBoard(filters)); }
    catch { setToast("状态已保存，列表刷新失败，请重试读取"); }
    setBusy(false); writing.current = false;
  }
  async function more(status: ResearchStatus, offset: number) {
    if (data === null || loading || writing.current) return;
    const version = generation.current;
    setLoading(true);
    try {
      const page = await fetchOpportunityBoard(filters, undefined, status, offset);
      if (version !== generation.current) return;
      const column = page.columns[0];
      setData(current => current === null ? null : { columns: current.columns.map(old => old.status === status
        ? { ...column, items: [...old.items, ...column.items] } : old) });
    } catch { if (version === generation.current) setToast("加载更多失败，请重试"); }
    finally { if (version === generation.current) setLoading(false); }
  }
  if (legacyCard) return <p role="status">正在打开信息卡…</p>;
  return <ReaderChrome active="opportunities" wide><div className="opportunities">
    <OpportunityHeader />
    <fieldset className="filters" disabled={busy}><label><input type="search" aria-label="搜索" placeholder="搜索标题、用处、商机内容"
      value={filters.query} onChange={event => change({ ...filters, query: event.target.value })} /></label>
      <label>子类型<select value={filters.subtype} onChange={event => change({ ...filters, subtype: event.target.value })}>
        <option value="">全部</option>{Object.entries(SUBTYPES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>平台<select value={filters.platform} onChange={event => change({ ...filters, platform: event.target.value })}>
        <option value="">全部</option>{platforms.map(platform => <option key={platform.platform} value={platform.platform}>{platform.label}</option>)}</select></label>
    </fieldset>
    {loading ? <p role="status">正在加载商机…</p> : null}
    {error ? <p role="alert">商机读取失败。<button className="btn" onClick={() => { setLoading(true); setRetry(value => value + 1); }}>重试</button></p> : null}
    {data !== null ? <div className="board" aria-busy={loading}>{data.columns.map(column => <section key={column.status}
      aria-label={column.label} className={`col ${column.status}${over === column.status ? " is-over" : ""}`}
      onDragOver={event => { if (dragId !== null) { event.preventDefault(); setOver(column.status); } }} onDragLeave={() => setOver(null)}
      onDrop={event => { event.preventDefault(); setOver(null); if (dragId !== null) {
        const item = data.columns.flatMap(value => value.items).find(row => row.knowledge_id === dragId);
        if (item !== undefined) void move(item, column.status); setDragId(null);
      } }}><div className="col__head"><i style={{ background: `var(--c-${column.status})` }} /><b>{column.label}</b><span>{column.total}</span></div>
      {column.items.map(item => <OpportunityTile key={item.knowledge_id} item={item} columns={data.columns} busy={busy || loading}
        onMove={target => void move(item, target)} onDrag={setDragId} />)}
      {!column.total ? <p className="empty">暂无商机</p> : null}
      {column.next_offset !== null ? <button className="btn col__more" disabled={loading || busy}
        onClick={() => void more(column.status, column.next_offset!)}>加载更多（{column.items.length} / {column.total}）</button> : null}
    </section>)}</div> : null}
    <div className={`opportunities__toast${toast ? " show" : ""}`} role="status">{toast}</div>
  </div></ReaderChrome>;
}
