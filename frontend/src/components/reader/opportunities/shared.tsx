export const SUBTYPES: Record<string, string> = {
  case_business: "复盘中的生意", running_ads: "在投广告", selling: "带货/售卖", wind_vane: "风向标短讯",
};
export const SIGNAL_LABELS: Record<string, string> = {
  revenue: "收入", price: "价格", sales: "销量", orders: "接单", deals: "成交", ads: "在投广告",
};
export function dateText(value: string) {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}
export function OpportunityHeader({ stats = false }: { stats?: boolean }) {
  return <header className="head"><div><h1>{stats ? "商机统计" : "商机看板"}</h1>
    <p>{stats ? "商机知识卡的调研状态、赚钱信号与来源分布。" : "按调研状态整理商机，拖动卡片即可更新状态。"}</p></div>
    <nav className="subnav" aria-label="商机视图"><a href="/r/opportunities" aria-current={!stats ? "page" : undefined}>商机看板</a>
      <a href="/r/opportunities/stats" aria-current={stats ? "page" : undefined}>统计</a></nav></header>;
}
