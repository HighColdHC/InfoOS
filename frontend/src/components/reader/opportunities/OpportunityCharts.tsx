import type { StatsData } from "../../../lib/types/opportunities";
import { NEXT_STEPS } from "./shared";

export function Funnel({ data }: { data: StatsData["funnel"] }) {
  const rows = [
    ["visible", "刷到", "抖音、小红书推荐流"], ["checked", "初筛检查", "规则读到正文"], ["matched", "命中线索", "含知识星球风向标"],
    ["broken_down", "agent 已拆解", "每卡最新拆解"], ["verify", "建议核实", "值得接触"], ["following", "决定跟进", "有过跟进决定"],
    ["contacted", "已接触", "有核实记录"], ["action", "转入行动", "转入 PKM"],
  ] as const;
  return <div className="funnel">{rows.map(([key, label, sub], index) => {
    const count = data[key], prior = index === 0 ? 0 : data[rows[index - 1][0]];
    const width = data.visible === 0 ? 0 : Math.min(100, count / data.visible * 100);
    return <div key={key} className={`fstep${count === 0 ? " fstep--zero" : key === "verify" ? " fstep--verify" : ""}`}>
      <span className="fstep__label"><b>{label}</b>{sub}</span><div className="fstep__track"><div className={`fstep__fill${count > 0 && width < 18 ? " is-thin" : ""}`} style={{ width: `${count === 0 ? 100 : Math.max(width, 1)}%` }}>{count.toLocaleString()}</div></div>
      <span className="fstep__rate">{index === 0 ? "" : prior ? `${Math.round(count / prior * 100)}%` : "—"}</span></div>;
  })}</div>;
}
export function DailyChart({ daily }: { daily: StatsData["daily"] }) {
  const max = Math.max(1, ...daily.map(day => day.count));
  return <div className="daily-scroll"><div className="bars" role="img" aria-label="每日拆解数">{daily.map(day => <div key={day.date} title={`${day.date}：${day.count} 条`}>
    <span>{day.count}</span><span className="b" style={{ height: `${day.count / max * 86}%` }} /><span>{day.date.slice(5)}</span></div>)}</div></div>;
}
export function EvaluationCharts({ data }: { data: StatsData }) {
  const max = Math.max(1, ...data.models.map(model => model.verify + model.observe + model.skip));
  const colors = { verify: "var(--verify)", observe: "var(--observe)", skip: "var(--skip)" };
  const x = (value: number) => 34 + value / 100 * 316, y = (value: number) => 210 - value * 2;
  return <div className="grid2"><div className="panel"><h3>变现方式 × 建议</h3><div className="stack">{data.models.map(model => <div className="stack__row" key={model.model}>
    <span>{model.label}</span><div className="stack__bar">{(["verify", "observe", "skip"] as const).map(step => <i key={step} style={{ width: `${model[step] / max * 100}%`, background: colors[step] }} title={`${NEXT_STEPS[step]} ${model[step]}`} />)}</div><span>{model.verify + model.observe + model.skip}</span></div>)}</div>
    <div className="legend">{(["verify", "observe", "skip"] as const).map(step => <span key={step}><i style={{ background: colors[step] }} />{NEXT_STEPS[step]}</span>)}</div></div>
    <div className="panel"><h3>可复制性 × 可信度</h3><svg className="scatter" viewBox="0 0 360 252" role="img" aria-label={`${data.points.length} 条拆解的可复制性与可信度分布`}>
      <rect x={x(60)} y={y(100)} width={x(100) - x(60)} height={y(60) - y(100)} fill="var(--verify-soft)" rx="4" /><text x={x(98)} y={y(96)} textAnchor="end">优先接触区</text>
      {[0, 50, 100].map(value => <g key={value}><line x1={x(0)} x2={x(100)} y1={y(value)} y2={y(value)} stroke="var(--border)" />
        <text x="28" y={y(value) + 4} textAnchor="end">{value}</text><text x={x(value)} y="226" textAnchor="middle">{value}</text></g>)}
      {data.points.map(([rep, cred, step], index) => <circle key={index} cx={x(rep) + ((index * 37) % 7 - 3) * 0.35} cy={y(cred) + ((index * 53) % 7 - 3) * 0.35} r="2.4" fill={colors[step]} fillOpacity={step === "verify" ? .85 : .4} />)}
      <text x="192" y="244" textAnchor="middle">可复制性 →</text><text x="10" y="110" textAnchor="middle" transform="rotate(-90 10 110)">可信度 →</text></svg>
      <p className="note">风险标记：{data.risks.map(risk => `${risk.label} ${risk.count}`).join(" · ")}</p></div></div>;
}
