import type { StatsData } from "../../../lib/types/opportunities";

export function DailyChart({ daily }: { daily: StatsData["daily"] }) {
  const max = Math.max(1, ...daily.map(day => day.count));
  return <div className="daily-scroll"><div className="bars" role="img" aria-label="每日新增商机数">{daily.map(day => <div key={day.date} title={`${day.date}：${day.count} 条`}>
    <span>{day.count}</span><span className="b" style={{ height: `${day.count / max * 86}%` }} /><span>{day.date.slice(5)}</span></div>)}</div></div>;
}
export function CountChart({ rows }: { rows: { key: string; label: string; count: number }[] }) {
  const max = Math.max(1, ...rows.map(row => row.count));
  return <div className="stack">{rows.map(row => <div className="stack__row" key={row.key}>
    <span>{row.label}</span><div className="stack__bar"><i style={{ width: `${row.count / max * 100}%`, background: "var(--accent)" }} /></div><span>{row.count}</span>
  </div>)}</div>;
}
