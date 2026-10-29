import "../../styles/_knowledge-reader.css";
import type { KnowledgeBrief } from "../../lib/types/knowledge";
import { KNOWLEDGE_LABELS } from "../../lib/types/knowledge";

const SECTIONS = [["news", "资讯"], ["opportunity", "商机"], ["project", "项目"], ["topic", "选题"], ["deposit", "沉淀"]] as const;

export function KnowledgeBriefContent({ brief }: { brief: KnowledgeBrief }) {
  const stock = Object.entries(brief.stock).filter(([, count]) => count > 0)
    .map(([kind, count]) => `${KNOWLEDGE_LABELS[kind]} +${count}`).join("、");
  return <article className="card-reader__article brief-reader knowledge-reader">
    <header className="card-reader__header">
      <p className="card-reader__eyebrow">InfoOS 日报 · 画像版本 {brief.profile_version}</p>
      <h1>{brief.brief_date}</h1>
      <p>北京时间前一日 07:00 至当日 07:00</p>
    </header>
    {SECTIONS.map(([key, label]) => <section className="brief-reader__group" key={key}>
      <div className="knowledge-reader__section-heading"><h2>{label} · 新增 {brief.counts[key]}</h2>
        <a href={`/r/knowledge?kind=${key === "deposit" ? "method" : key}`}>查看全部</a></div>
      {brief.items.filter(item => item.section === key).map(item => <article className="brief-reader__item" key={item.knowledge_id}>
        <div className="brief-reader__item-body">
          <p className="brief-reader__chips">{KNOWLEDGE_LABELS[item.kind]} · 证据强度 {item.strength}{item.is_update ? " · 更新" : ""}</p>
          <h3><a href={item.deep_link}>{item.number}. {item.title}</a></h3>
          <p>{item.use_note}</p>
          {item.attribution_ref !== null ? <p>归属：{item.attribution_ref}</p> : null}
          <a href={item.origin_card_link}>查看原卡</a>
        </div>
      </article>)}
      {!brief.items.some(item => item.section === key) ? <p>今日无条目</p> : null}
    </section>)}
    <p className="knowledge-reader__stock">存量入库：{stock || "无"}</p>
  </article>;
}
