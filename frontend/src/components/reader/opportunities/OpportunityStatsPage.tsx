import "../../../styles/_card-reader.css";
import "../../../styles/_opportunities.css";
import { useEffect, useState } from "react";
import { fetchOpportunityStats } from "../../../lib/api/opportunities";
import type { StatsData, StatsWindow } from "../../../lib/types/opportunities";
import { ReaderChrome } from "../ReaderChrome";
import { CountChart, DailyChart } from "./OpportunityCharts";
import { OpportunityHeader, SIGNAL_LABELS } from "./shared";

export function OpportunityStatsPage() {
  const [window, setWindow] = useState<StatsWindow>("all");
  const [data, setData] = useState<StatsData | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetchOpportunityStats(window, controller.signal).then(value => { if (!controller.signal.aborted) { setData(value); setLoading(false); setError(false); } },
      () => { if (!controller.signal.aborted) { setError(true); setLoading(false); } });
    return () => controller.abort();
  }, [window, retry]);
  return <ReaderChrome active="opportunities" wide><div className="opportunities opportunities-stats"><OpportunityHeader stats />
    <div className="hero__row"><div className="seg" role="group" aria-label="时间范围">{([ ["7d", "近 7 天"], ["30d", "近 30 天"], ["all", "全部"] ] as const).map(([key, name]) => <button key={key} aria-pressed={window === key} onClick={() => { if (key !== window) { setLoading(true); setWindow(key); } }}>{name}</button>)}</div>
      <span className="hint">按创建时间筛选，上海时区自然日</span></div>
    {loading ? <p role="status">正在加载统计…</p> : null}
    {error ? <p role="alert">统计读取失败。<button className="btn" onClick={() => { setLoading(true); setRetry(value => value + 1); }}>重试</button></p> : null}
    {data !== null && !loading && !error ? <>
      <p>商机知识卡共 {data.total} 张</p>
      <section className="block"><div className="block__head"><h2>调研状态分布</h2></div><div className="panel"><CountChart rows={data.by_status.map(row => ({ ...row, key: row.status }))} /></div></section>
      <section className="block"><div className="block__head"><h2>子类型分布</h2></div><div className="panel"><CountChart rows={data.by_subtype.map(row => ({ ...row, key: row.subtype }))} /></div></section>
      <section className="block"><div className="block__head"><h2>赚钱信号类型</h2></div><div className="panel"><CountChart rows={data.by_signal.map(row => ({ ...row, key: row.signal, label: SIGNAL_LABELS[row.signal] }))} /></div></section>
      <section className="block"><div className="block__head"><h2>每日新增</h2></div><div className="panel"><DailyChart daily={data.daily} /></div></section>
      <section className="block"><div className="block__head"><h2>来源平台</h2></div><div className="panel"><CountChart rows={data.by_platform.map(row => ({ ...row, key: row.platform }))} /></div></section>
    </> : null}
  </div></ReaderChrome>;
}
