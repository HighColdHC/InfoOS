import "../../styles/_card-reader.css";
import "../../styles/_knowledge-reader.css";
import { useEffect, useState } from "react";
import { getJson } from "../../lib/api/http";
import type { FieldRule, FieldValue, KnowledgeCard } from "../../lib/types/knowledge";
import { KNOWLEDGE_LABELS, RESEARCH_LABELS } from "../../lib/types/knowledge";
import { platformLabel } from "../../lib/platforms";
import { formatCardPreviewDate } from "../modules/collector/cardPreview/cardPreviewDate";
import { ReaderChrome } from "./ReaderChrome";

const ACTIONS: Record<string, string> = { new: "新建", merge: "归并", update: "更新", conflict: "冲突" };

function Field({ value, rule }: { value: FieldValue; rule: FieldRule }) {
  if (value === null) return <span>未知</span>;
  if (Array.isArray(value)) return value.length ? <ul>{value.map((part, i) => <li key={i}>{part}</li>)}</ul> : <span>无</span>;
  if (typeof value === "object" && rule.properties) return <dl>{Object.entries(rule.properties).map(([key, child]) =>
    <div key={key}><dt>{child.label}</dt><dd>{value[key] === null ? "未知" : value[key]}</dd></div>)}</dl>;
  return <span>{String(value)}</span>;
}

export function KnowledgeReaderPage() {
  const segment = window.location.pathname.match(/^\/r\/knowledge\/([^/]+)\/?$/)?.[1];
  const [card, setCard] = useState<KnowledgeCard | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!segment) { setError("知识卡地址无效"); return; }
    const controller = new AbortController();
    void getJson<KnowledgeCard>(`/api/intelligence/knowledge/${segment}`, controller.signal)
      .then(value => { if (!controller.signal.aborted) setCard(value); }, () => {
        if (!controller.signal.aborted) setError("知识卡不存在或暂时无法读取");
      });
    return () => controller.abort();
  }, [segment]);
  return <ReaderChrome active="knowledge">
    {error ? <div className="card-reader__message" role="status"><h1>{error}</h1></div> : null}
    {!card && !error ? <p role="status">正在加载知识卡…</p> : null}
    {card ? <article className="card-reader__article knowledge-reader">
      <header className="card-reader__header">
        <p className="card-reader__eyebrow"><a href={`/r/knowledge?kind=${card.kind}`}>{card.kind_label}</a> · {card.subtype_label}</p>
        <h1>{card.title}</h1>
        <p>归属：{card.attribution_ref === null ? "暂无归属" : card.attribution_ref}</p>
        <p>{card.use_note}</p>
        {card.has_conflict ? <p className="knowledge-reader__conflict" role="status">有冲突证据</p> : null}
      </header>
      <section aria-label="结构化字段"><dl className="knowledge-reader__fields">
        {Object.entries(card.field_schema).map(([key, rule]) => <div key={key}>
          <dt>{rule.label}</dt><dd><Field value={card.fields[key]} rule={rule} /></dd>
        </div>)}
      </dl></section>
      {card.kind === "opportunity" && card.research_status !== null ? <section>
        <h2>调研状态：{RESEARCH_LABELS[card.research_status]}</h2>
        <ul>{card.status_history.map(event => <li key={event.event_id}>
          {RESEARCH_LABELS[event.value]} · {formatCardPreviewDate(event.created_at)} · {event.actor}
          {event.note !== null ? <p>{event.note}</p> : null}
        </li>)}</ul>
      </section> : null}
      <section><h2>原文证据</h2>{card.evidence.map(evidence => <article className="knowledge-reader__evidence" key={evidence.evidence_id}>
        <h3><a href={evidence.deep_link}>{evidence.title}</a></h3>
        <p>{platformLabel(evidence.platform)} · {evidence.published_at === null ? "发布时间未知" : formatCardPreviewDate(evidence.published_at)} · {ACTIONS[evidence.action]}</p>
        {evidence.quotes.map((quote, i) => <blockquote key={i}>{quote}</blockquote>)}
        {evidence.note !== null ? <p>{evidence.note}</p> : null}
      </article>)}</section>
      <section><h2>同源知识卡</h2>{card.related_cards.length ? <ul>{card.related_cards.map(related =>
        <li key={related.knowledge_id}>{KNOWLEDGE_LABELS[related.kind]} · <a href={related.deep_link}>{related.title}</a></li>)}</ul> : <p>暂无同源知识卡</p>}</section>
    </article> : null}
  </ReaderChrome>;
}
