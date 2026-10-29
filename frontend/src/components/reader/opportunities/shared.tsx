import type { Stage } from "../../../lib/types/opportunities";

export const STAGES: { key: Stage; name: string; desc: string }[] = [
  { key: "inbox", name: "新推荐", desc: "agent 建议核实，等你决定跟不跟" },
  { key: "plan", name: "准备接触", desc: "决定跟进，还没联系对方" },
  { key: "contact", name: "已接触 · 观察中", desc: "有核实记录，结论是继续观察" },
  { key: "action", name: "转入行动", desc: "已转到 PKM 执行" },
  { key: "archive", name: "归档", desc: "暂不跟进或接触后放弃" },
];
export const RECORD_HINT = "这一步要靠核实记录：把联系结果发给 agent，写回后自动换列";
export const NEXT_STEPS = { verify: "值得接触核实", observe: "持续观察", skip: "放弃" };
export function dateText(value: string | null) {
  return value === null ? "—" : new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}
export function OpportunityHeader({ stats = false }: { stats?: boolean }) {
  const navigation = <nav className="subnav" aria-label="商机视图"><a href="/r/opportunities" aria-current={!stats ? "page" : undefined}>跟进看板</a>
    <a href="/r/opportunities/stats" aria-current={stats ? "page" : undefined}>统计</a></nav>;
  if (stats) return <header className="hero"><p className="eyebrow">Opportunity · 统计</p><h1>商机统计</h1>
    <p className="lede">从刷到内容、规则初筛、agent 拆解到你的跟进，每一层各有多少。日常跟进请到<a href="/r/opportunities">跟进看板</a>。</p>{navigation}</header>;
  return <header className="head"><div><h1>商机跟进</h1>
    <p>agent 推荐的线索在这里决定跟不跟、去联系、记录结果，直到放弃或转入 PKM 行动。</p></div>{navigation}</header>;
}
