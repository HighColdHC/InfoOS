import "../../../styles/_card-reader.css";
import "../../../styles/_opportunities.css";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchOpportunityBoard, setOpportunityFollowUp } from "../../../lib/api/opportunities";
import type { BoardData, BoardFilters, CandidateKind, OpportunityDetail, OpportunityItem, Stage } from "../../../lib/types/opportunities";
import { ReaderChrome } from "../ReaderChrome";
import { AllOpportunities } from "./AllOpportunities";
import { OpportunityDrawer } from "./OpportunityDrawer";
import { OpportunityTile } from "./OpportunityTile";
import { moveBoard, resetStage } from "./boardState";
import { OpportunityHeader, RECORD_HINT, STAGES } from "./shared";

export function OpportunityBoardPage() {
  const [filters, setFilters] = useState<BoardFilters>({ query: "", platform: "", monetization_model: "", include_observe: false, candidate_kind: "" });
  const [data, setData] = useState<BoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const writing = useRef(false);
  const generation = useRef(0);
  const [active, setActive] = useState<Stage>("inbox");
  const [dragId, setDragId] = useState<string | null>(null);
  const [over, setOver] = useState<Stage | null>(null);
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(window.location.search).get("card"));
  const [view, setView] = useState<"board" | "all">("board");
  const [revision, setRevision] = useState(0);
  const [toast, setToast] = useState("");
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notify = useCallback((text: string) => {
    setToast(text);
    if (toastTimer.current !== null) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 3000);
  }, []);
  const close = useCallback(() => {
    const url = new URL(window.location.href); url.searchParams.delete("card");
    window.history.replaceState({}, "", url); setSelected(null);
  }, []);
  function open(cardId: string) {
    const url = new URL(window.location.href); url.searchParams.set("card", cardId);
    window.history.pushState({}, "", url); setSelected(cardId);
  }
  useEffect(() => {
    const pop = () => setSelected(new URLSearchParams(window.location.search).get("card"));
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  useEffect(() => () => { generation.current++; if (toastTimer.current !== null) clearTimeout(toastTimer.current); }, []);
  useEffect(() => {
    const controller = new AbortController();
    void fetchOpportunityBoard(filters, controller.signal).then(value => {
      if (!controller.signal.aborted) { setData(value); setLoading(false); setError(false); }
    }, () => { if (!controller.signal.aborted) { setLoading(false); setError(true); } });
    return () => controller.abort();
  }, [filters, retry]);
  function change(next: BoardFilters) { generation.current++; setLoading(true); setError(false); setFilters(next); }
  function jump(stage: Stage) {
    setActive(stage);
    document.getElementById(`opportunity-col-${stage}`)?.scrollIntoView({ block: "nearest", inline: "start", behavior: "smooth" });
  }
  async function move(item: OpportunityItem, target: Stage) {
    if (target === "contact" || target === "action") { notify(RECORD_HINT); return; }
    if (target === "inbox" && (item.stage === "contact" || item.stage === "action")) { notify("已有核实记录，阶段由最新核实结果决定；把新结果告诉 agent"); return; }
    if (data === null || writing.current || loading || target === item.stage) return;
    const stage = target === "inbox" ? resetStage(item, filters.include_observe) : target;
    const before = data;
    const optimistic = moveBoard(data, item, stage, target === "inbox");
    writing.current = true; setBusy(true); setData(optimistic);
    try {
      await setOpportunityFollowUp(item.card_id, target === "plan" ? "following" : target === "archive" ? "dismissed" : "reset");
    } catch {
      setData(before); setBusy(false); writing.current = false;
      notify("跟进状态保存失败，已恢复原状态，请重试"); return;
    }
    setRevision(value => value + 1);
    notify(target === "plan" ? "已移到“准备接触”" : target === "archive" ? "已归档，可随时恢复" : "已恢复自动阶段");
    // Refresh facts after a successful write; a failed refresh must not undo the saved decision.
    try {
      const refreshed = await fetchOpportunityBoard(filters);
      setData(refreshed);
    } catch { notify("状态已保存，列表刷新失败，请重试读取"); }
    setBusy(false); writing.current = false;
  }
  async function moveDetail(detail: OpportunityDetail, target: Stage) {
    const item = data === null ? undefined : Object.values(data.columns).flatMap(column => column.items).find(row => row.card_id === detail.card_id);
    if (item !== undefined) { await move(item, target); return; }
    if (target === "contact" || target === "action") { notify(RECORD_HINT); return; }
    if (target === "inbox" && (detail.stage === "contact" || detail.stage === "action")) { notify("已有核实记录，阶段由最新核实结果决定；把新结果告诉 agent"); return; }
    if (writing.current || target === detail.stage) return;
    writing.current = true; setBusy(true);
    try {
      await setOpportunityFollowUp(detail.card_id, target === "plan" ? "following" : target === "archive" ? "dismissed" : "reset");
      setRevision(value => value + 1); setRetry(value => value + 1); notify("跟进状态已保存");
    } catch { notify("跟进状态保存失败，请重试"); }
    setBusy(false); writing.current = false;
  }
  async function more() {
    if (data === null || loading || writing.current) return;
    const version = generation.current;
    setLoading(true);
    try {
      const page = await fetchOpportunityBoard(filters, undefined, 20, data.columns.inbox.items.length);
      if (version !== generation.current) return;
      setData({ ...page, columns: { ...page.columns, inbox: { ...page.columns.inbox, items: [...data.columns.inbox.items, ...page.columns.inbox.items] } } });
    } catch { if (version === generation.current) notify("加载更多失败，请重试"); }
    if (version === generation.current) setLoading(false);
  }
  return <ReaderChrome active="opportunities" wide><div className="opportunities">
    <OpportunityHeader />
    <div className="opportunity-views" role="group" aria-label="商机视图">
      <button className="btn" aria-pressed={view === "board"} onClick={() => setView("board")}>看板</button>
      <button className="btn" aria-pressed={view === "all"} onClick={() => setView("all")}>全部</button>
    </div>
    {view === "all" ? <AllOpportunities revision={revision} busy={busy} onOpen={open} /> : <>
    {data !== null ? <div className="today">{([
      ["inbox", data.today.inbox, "新推荐待决定", "过去 24 小时 agent 新推荐"],
      ["plan", data.today.plan_stale, "准备接触超过 3 天", "还没联系，要不要放弃"],
      ["contact", data.today.contact, "等对方回复", "回复后把结果告诉 agent"],
      ["action", data.today.action, "已转入行动", "在 PKM 里推进"],
    ] as const).map(([stage, count, title, subtitle]) => <button key={stage} className={stage === "plan" && count ? "is-warn" : ""} onClick={() => jump(stage)}><b>{count}</b><span><strong>{title}</strong>{subtitle}</span></button>)}</div> : null}
    <fieldset className="filters" disabled={busy}><label><input type="search" aria-label="搜索" placeholder="搜索卡号、标题、要约、账号、来源" value={filters.query} onChange={event => change({ ...filters, query: event.target.value })} /></label>
      <label>平台<select value={filters.platform} onChange={event => change({ ...filters, platform: event.target.value })}><option value="">全部</option>
        {data !== null ? data.platforms.map(platform => <option key={platform.platform} value={platform.platform}>{platform.label}</option>) : null}</select></label>
      <label>变现方式<select value={filters.monetization_model} onChange={event => change({ ...filters, monetization_model: event.target.value })}><option value="">全部</option>
        {data !== null ? Object.entries(data.models).map(([key, label]) => <option key={key} value={key}>{label}</option>) : null}</select></label>
      <label>候选类型<select value={filters.candidate_kind} onChange={event => change({ ...filters, candidate_kind: event.target.value as CandidateKind | "" })}>
        <option value="">全部</option><option value="prefilter">初筛线索</option><option value="wind_vane">风向标</option><option value="case">案例</option></select></label>
      <label>新推荐范围<select value={String(filters.include_observe)} onChange={event => change({ ...filters, include_observe: event.target.value === "true" })}>
        <option value="false">agent 建议核实</option><option value="true">也包括“持续观察”</option></select></label></fieldset>
    {loading ? <p role="status">正在加载商机…</p> : null}
    {error ? <p role="alert">商机读取失败。<button className="btn" onClick={() => { setLoading(true); setRetry(value => value + 1); }}>重试</button></p> : null}
    {data !== null ? <><div className="coltabs" role="group" aria-label="切换列">{STAGES.map(stage => <button key={stage.key} aria-pressed={active === stage.key} onClick={() => jump(stage.key)}>{stage.name} {data.columns[stage.key].count}</button>)}</div>
      <div className="board" aria-busy={loading}>{STAGES.map(stage => <section key={stage.key} id={`opportunity-col-${stage.key}`} aria-label={stage.name}
        className={`col ${stage.key}${active === stage.key ? " is-active" : ""}${over === stage.key ? " is-over" : ""}`}
        onDragOver={event => { if (dragId !== null) { event.preventDefault(); setOver(stage.key); } }} onDragLeave={() => setOver(null)}
        onDrop={event => { event.preventDefault(); setOver(null); if (dragId !== null) {
          const item = Object.values(data.columns).flatMap(column => column.items).find(row => row.card_id === dragId);
          if (item !== undefined) void move(item, stage.key); setDragId(null);
        } }}><div className="col__head"><i style={{ background: `var(--c-${stage.key})` }} /><b>{stage.name}</b><span>{data.columns[stage.key].count}</span></div>
        <p className="col__desc">{stage.key === "inbox" && filters.include_observe ? "建议核实与持续观察，等你决定跟不跟" : stage.desc}</p>
        {data.columns[stage.key].items.map(item => <OpportunityTile key={item.card_id} item={item} busy={busy || loading} onOpen={() => open(item.card_id)} onMove={target => void move(item, target)} onDrag={setDragId} />)}
        {!data.columns[stage.key].count ? <p className="empty">暂无商机</p> : null}
        {stage.key === "inbox" && data.columns.inbox.items.length < data.columns.inbox.count ? <button className="btn col__more" disabled={loading || busy} onClick={() => void more()}>加载更多（{data.columns.inbox.items.length} / {data.columns.inbox.count}）</button> : null}
      </section>)}</div></> : null}
    </>}
    {selected !== null ? <OpportunityDrawer key={selected} cardId={selected} revision={revision} busy={busy || loading} onClose={close} onMove={(detail, stage) => void moveDetail(detail, stage)} notify={notify} /> : null}
    <div className={`opportunities__toast${toast ? " show" : ""}`} role="status">{toast}</div>
  </div></ReaderChrome>;
}
