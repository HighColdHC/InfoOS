import "../../../styles/_card-reader.css";
import "../../../styles/_opportunities.css";
import { useEffect, useState } from "react";
import { fetchOpportunityStats } from "../../../lib/api/opportunities";
import type { StatsData, StatsWindow } from "../../../lib/types/opportunities";
import { ReaderChrome } from "../ReaderChrome";
import { DailyChart, EvaluationCharts, Funnel } from "./OpportunityCharts";
import { dateText, NEXT_STEPS, OpportunityHeader } from "./shared";

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
    <div className="hero__row"><div className="seg" role="group" aria-label="时间范围">{([ ["7d", "近 7 天"], ["30d", "近 30 天"], ["all", "全部"] ] as const).map(([key, name]) => <button key={key} aria-pressed={window === key} onClick={() => { if (key !== window) { setLoading(true); setWindow(key); } }}>{name}</button>)}</div><span className="hint">按 Asia/Shanghai 日界统计</span></div>
    {loading ? <p role="status">正在加载统计…</p> : null}
    {error ? <p role="alert">统计读取失败。<button className="btn" onClick={() => { setLoading(true); setRetry(value => value + 1); }}>重试</button></p> : null}
    {data !== null && !loading && !error ? <>
      <section className="block"><div className="block__head"><h2>线索漏斗<small>从发现到行动</small></h2><p className="hint">每层右侧为占上一层比例</p></div>
        <div className="grid2"><div className="panel"><Funnel data={data.funnel} /><p className="note">运行与卡片按各自事实时间计数；重复刷到、历史积累与跨期跟进会使漏斗比例超过 100%。</p></div>
          <div className="panel"><h3>按平台</h3><div className="tablewrap"><table><thead><tr><th>平台</th><th>刷到</th><th>命中</th><th>已拆解</th><th>建议核实</th></tr></thead><tbody>
            {data.platforms.map(platform => <tr key={platform.platform}><td>{platform.label}</td>{[platform.visible, platform.matched, platform.broken_down, platform.verify].map((count, index) => <td className="num" key={index}>{count.toLocaleString()}</td>)}</tr>)}</tbody></table></div>
            <h3 className="chart-title">每日拆解</h3><DailyChart daily={data.daily} /></div></div></section>
      <section className="block"><div className="block__head"><h2>拆解进度</h2><p className="hint">当前画像版本下的候选</p></div><div className="panel kpis">
        <div className="kpi"><span>最近一次拆解</span><b className="time-value">{dateText(data.progress.latest_breakdown_at)}</b></div>
        <div className="kpi"><span>近 24 小时拆解</span><b>{data.progress.last_24h}</b></div><div className="kpi"><span>当前待拆解候选</span><b>{data.progress.pending}</b></div></div></section>
      <section className="block"><div className="block__head"><h2>agent 怎么判断</h2></div><div className="tabs">{(["verify", "observe", "skip"] as const).map(step => <div key={step} className={`tab tab--${step}`}><b>{data.next_steps[step].toLocaleString()}</b><span>{NEXT_STEPS[step]}</span></div>)}</div>
        <EvaluationCharts data={data} /></section>
      <section className="block"><div className="block__head"><h2>跟进转化<small>来自跟进状态与核实记录</small></h2></div><div className="panel"><div className="kpis">
        <div className="kpi"><span>新推荐待决定</span><b>{data.follow_up.stages.inbox}</b><small>agent 建议核实、你还没决定</small></div>
        <div className="kpi"><span>决定跟进</span><b>{data.funnel.following}</b><small>跟进率 {data.funnel.verify ? `${Math.round(data.funnel.following / data.funnel.verify * 100)}%` : "—"}（占建议核实）</small></div>
        <div className="kpi"><span>已接触</span><b>{data.funnel.contacted}</b><small>有核实记录的商机</small></div>
        <div className="kpi"><span>转入行动</span><b>{data.funnel.action}</b><small>占已接触 {data.funnel.contacted ? `${Math.round(data.funnel.action / data.funnel.contacted * 100)}%` : "—"}</small></div>
        <div className="kpi"><span>归档</span><b>{data.follow_up.stages.archive}</b><small>暂不跟进 {data.follow_up.archive.dismissed} · 接触后放弃 {data.follow_up.archive.dropped}</small></div>
        <div className="kpi"><span>决定到首次接触</span><b>{data.follow_up.median_contact_days === null ? "—" : data.follow_up.median_contact_days.toFixed(1)}</b><small>中位数，单位：天</small></div>
      </div></div></section>
    </> : null}
  </div></ReaderChrome>;
}
