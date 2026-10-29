import { useEffect, useState } from "react";
import { fetchAllOpportunities } from "../../../lib/api/opportunities";
import type { CandidateKind, OpportunityListFilters, OpportunityPage } from "../../../lib/types/opportunities";
import { OpportunitySource } from "./OpportunitySource";
import { dateText, STAGES } from "./shared";

const CANDIDATE_KINDS: Record<CandidateKind, string> = { prefilter: "初筛线索", wind_vane: "风向标", case: "案例" };

export function AllOpportunities({ revision, busy, onOpen }: { revision: number; busy: boolean; onOpen: (id: string) => void }) {
  const [filters, setFilters] = useState<OpportunityListFilters>({ query: "", platform: "", monetization_model: "", next_step: "", stage: "", candidate_kind: "" });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<OpportunityPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetchAllOpportunities(filters, offset, controller.signal).then(value => {
      if (!controller.signal.aborted) { setData(value); setLoading(false); setError(false); }
    }, () => { if (!controller.signal.aborted) { setLoading(false); setError(true); } });
    return () => controller.abort();
  }, [filters, offset, revision, retry]);
  function change<K extends keyof OpportunityListFilters>(key: K, value: OpportunityListFilters[K]) {
    setLoading(true); setFilters(current => ({ ...current, [key]: value })); setOffset(0);
  }
  return <section aria-label="全部商机">
    <fieldset className="filters" disabled={busy}>
      <label><input type="search" aria-label="搜索全部商机" placeholder="搜索卡号、标题、要约、账号、来源" value={filters.query} onChange={event => change("query", event.target.value)} /></label>
      <label>建议<select value={filters.next_step} onChange={event => change("next_step", event.target.value)}><option value="">全部</option><option value="verify">建议核实</option><option value="observe">持续观察</option><option value="skip">暂不考虑</option></select></label>
      <label>阶段<select value={filters.stage} onChange={event => change("stage", event.target.value)}><option value="">全部</option>{STAGES.map(stage => <option key={stage.key} value={stage.key}>{stage.name}</option>)}<option value="unstaged">未进入看板</option></select></label>
      <label>候选类型<select value={filters.candidate_kind} onChange={event => change("candidate_kind", event.target.value as CandidateKind | "")}><option value="">全部</option>{Object.entries(CANDIDATE_KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>平台<select value={filters.platform} onChange={event => change("platform", event.target.value)}><option value="">全部</option>{data !== null ? data.platforms.map(platform => <option key={platform.platform} value={platform.platform}>{platform.label}</option>) : null}</select></label>
      <label>变现方式<select value={filters.monetization_model} onChange={event => change("monetization_model", event.target.value)}><option value="">全部</option>{data !== null ? Object.entries(data.models).map(([key, label]) => <option key={key} value={key}>{label}</option>) : null}</select></label>
    </fieldset>
    {loading ? <p role="status">正在加载商机…</p> : null}
    {error ? <p role="alert">商机读取失败。<button className="btn" onClick={() => { setLoading(true); setRetry(value => value + 1); }}>重试</button></p> : null}
    {data !== null && !loading && !error ? <>
      <p>共 {data.total} 张商机卡 · 按拆解时间倒序</p>
      <div className="opportunity-list">{data.items.map(item => <article className="tile" key={item.card_id}>
        <div className="tile__top"><span className="chip chip--model">{item.model_label}</span><span>{{ verify: "建议核实", observe: "持续观察", skip: "暂不考虑" }[item.next_step]}</span>
          <span>{STAGES.find(stage => stage.key === item.stage)?.name ?? "未进入看板"}</span>
          {item.candidate_kind !== null ? <span className="opportunity-list__candidate-source" aria-label="候选来源">{CANDIDATE_KINDS[item.candidate_kind]}</span> : null}</div>
        <OpportunitySource source={item} />
        <h3><button className="opportunity-list__open" onClick={() => onOpen(item.card_id)}>{item.offer}</button></h3>
        <p>{item.title}</p><div className="tile__top"><span>{item.card_id}</span><time dateTime={item.created_at}>拆解于 {dateText(item.created_at)}</time></div>
      </article>)}</div>
      {data.total === 0 ? <p className="empty">暂无匹配商机</p> : null}
      <nav className="opportunity-list__pagination" aria-label="全部商机分页">
        <button className="btn" disabled={offset === 0} onClick={() => { setLoading(true); setOffset(offset - 20); }}>上一页</button>
        <span>{Math.floor(offset / 20) + 1} / {Math.max(1, Math.ceil(data.total / 20))}</span>
        <button className="btn" disabled={offset + data.items.length >= data.total} onClick={() => { setLoading(true); setOffset(offset + 20); }}>下一页</button>
      </nav>
    </> : null}
  </section>;
}
