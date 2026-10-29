import "../../styles/_card-reader.css";
import "../../styles/_brief-reader.css";
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { formatCardPreviewDate } from "../modules/collector/cardPreview/cardPreviewDate";
import { getJson } from "../../lib/api/http";
import { ACTION, ATTRIBUTION, CONTENT_TYPE, enumLabel } from "../../lib/intelligenceLabels";
import { platformLabel } from "../../lib/platforms";
import { latestBriefDate, shiftDate } from "../../lib/briefDate";
import type { KnowledgeBrief } from "../../lib/types/knowledge";
import { KnowledgeBriefContent } from "./KnowledgeBriefContent";
import { ReaderChrome } from "./ReaderChrome";
import { PlatformMark } from "../ui/PlatformMark";
import { ScoreRing } from "../ui/ScoreRing";
import { CardReaderModal, type CardReaderTarget } from "./CardReaderModal";
import { cardReaderLinkProps } from "./cardReaderLink";

type BriefItem = {
  number: number; group: string; card_id: string; title: string; platform: string;
  author: string | null; published_at: string | null; summary: string;
  relation_note: string; attribution_kind: string; attribution_ref: string | null;
  content_type: string; suggested_action: string; quality: number;
  relevance: number | null; paused_profile_text?: string;
};
type Brief = {
  layout?: never;
  brief_date: string; window_start: string; window_end: string; profile_version: number;
  items: BriefItem[];
  // Frozen historical JSON may omit counts or individual groups.
  counts?: Partial<Record<string, number>>;
  weekly_paused?: { count: number; top: { title: string; deep_link: string } | null } | null;
};
const groups = [
  ["related", "相关条目", "命中你的项目、KR 与正在追问的问题"],
  ["exploration", "新方向", "与画像无关但质量足够高，值得扫一眼"],
  ["landmark", "格局变化", "暂放方向里出现的格局级信号"],
] as const;
const WEEKDAYS = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];

function dateParts(value: string) {
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return null;
  return { month: date.getUTCMonth() + 1, day: date.getUTCDate(), year: date.getUTCFullYear(), weekday: WEEKDAYS[date.getUTCDay()] };
}

export function BriefReaderPage() {
  const [readerTarget, setReaderTarget] = useState<CardReaderTarget | null>(null);
  const openReader = (cardId: string) => setReaderTarget({ cardId });
  const date = window.location.pathname.match(/^\/r\/brief\/([^/]+)\/?$/)?.[1];
  const [brief, setBrief] = useState<Brief | KnowledgeBrief | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!date) { setError("日报日期无效"); return; }
    // 保留原 request.ts 的语义：null 表示不设超时。
    void getJson<Brief | KnowledgeBrief>(`/api/intelligence/daily-brief/${encodeURIComponent(date)}`, undefined, null)
      .then(setBrief, () => setError("日报不存在或暂时无法读取"));
  }, [date]);
  const parts = date ? dateParts(date) : null;
  const dayNav = date && parts ? (
    <nav className="brief-reader__daynav" aria-label="切换日期">
      <a href={`/r/brief/${shiftDate(date, -1)}`}><ChevronLeft size={15} aria-hidden="true" />前一天</a>
      {date >= latestBriefDate()
        ? <span aria-disabled="true">后一天<ChevronRight size={15} aria-hidden="true" /></span>
        : <a href={`/r/brief/${shiftDate(date, 1)}`}>后一天<ChevronRight size={15} aria-hidden="true" /></a>}
    </nav>
  ) : null;
  if (brief && brief.layout === "knowledge_v1") return <ReaderChrome active="brief">
    <KnowledgeBriefContent brief={brief} />{dayNav}
  </ReaderChrome>;
  return <ReaderChrome active="brief">
    {error ? <div className="card-reader__message" role="status"><span className="card-reader__message-mark" aria-hidden="true" /><h1>{error}</h1>
      <p>日报每天北京时间 07:00 冻结；尚未生成或已超出保留范围时会显示这里。</p>{dayNav}</div> : null}
    {!brief && !error ? <div className="brief-reader__loading" role="status"><span className="spinner" aria-hidden="true" />正在加载日报…</div> : null}
    {brief ? <article className="card-reader__article brief-reader">
      <header className="card-reader__header brief-reader__hero">
        <p className="card-reader__eyebrow">InfoOS 日报 · {parts ? `${parts.year} 年 · ${parts.weekday}` : brief.brief_date}</p>
        <h1 aria-label={brief.brief_date}>{parts ? <><span>{parts.month}</span><small>月</small><span>{parts.day}</span><small>日</small></> : brief.brief_date}</h1>
        <p className="card-reader__meta">北京时间前一日 07:00 至当日 07:00 · 画像版本 {brief.profile_version}</p>
        <div className="brief-reader__stats">
          {groups.map(([key, label]) => {
            const count = brief.counts?.[key] ?? brief.items.filter(item => item.group === key).length;
            const content = <><strong>{count}</strong><span>{label}</span></>;
            return count > 0
              ? <a key={key} href={`#brief-${key}`} className="brief-reader__stat">{content}</a>
              : <div key={key} className="brief-reader__stat brief-reader__stat--empty">{content}</div>;
          })}
        </div>
        {dayNav}
      </header>
      {groups.map(([key, label, hint]) => {
        const items = brief.items.filter(item => item.group === key);
        if (!items.length) return <section className="brief-reader__group brief-reader__group--empty" key={key} data-group={key}>
          <h2>{label} · 今日无条目</h2>
        </section>;
        return <section className="brief-reader__group" key={key} id={`brief-${key}`} data-group={key}>
          <div className="brief-reader__group-head">
            <h2>{label} ({brief.counts?.[key] ?? items.length})</h2>
            <p>{hint}</p>
          </div>
          {items.map(item => <article className="brief-reader__item" key={item.card_id}>
            <div className="brief-reader__item-scores" aria-hidden="true">
              <span className="brief-reader__ring"><ScoreRing value={item.quality} label="质量" size={42} /><small>质量</small></span>
              <span className="brief-reader__ring"><ScoreRing value={item.relevance} label="相关" size={42} /><small>相关</small></span>
            </div>
            <div className="brief-reader__item-body">
              <p className="brief-reader__item-meta"><PlatformMark platform={item.platform} size="sm" />{platformLabel(item.platform)} · {item.author || "作者未记录"} · {item.published_at ? formatCardPreviewDate(item.published_at) : "发布时间未知"}</p>
              <h3><a {...cardReaderLinkProps(item.card_id, openReader)}><span className="brief-reader__num">{item.number}.</span> {item.title}</a></h3>
              <p>{item.summary}</p>
              <p className="brief-reader__relation">与你的关系：{item.relation_note}</p>
              <p className="brief-reader__chips">
                <span className="signal-chip signal-chip--accent">归属：{enumLabel(ATTRIBUTION, item.attribution_kind)}{item.attribution_ref ? ` · ${item.attribution_ref}` : ""}</span>
                <span className="signal-chip">{enumLabel(CONTENT_TYPE, item.content_type)}</span>
                <span className="signal-chip signal-chip--outline">AI 建议：{enumLabel(ACTION, item.suggested_action)}</span>
                <span className="brief-reader__scores-text">质量 {item.quality} · 相关 {item.relevance ?? "—"}</span>
              </p>
              {item.paused_profile_text ? <blockquote>暂放条目：{item.paused_profile_text}</blockquote> : null}
            </div>
          </article>)}
        </section>;
      })}
      {brief.weekly_paused ? <section className="brief-reader__group brief-reader__weekly"><div className="brief-reader__group-head"><h2>暂放方向周汇总</h2></div>
        <p>过去 7 天沉底 {brief.weekly_paused.count} 条
          {brief.weekly_paused.top ? <> · 最值得一看：<a href={brief.weekly_paused.top.deep_link}>{brief.weekly_paused.top.title}</a></> : null}</p>
      </section> : null}
    </article> : null}
    <CardReaderModal target={readerTarget} onClose={() => setReaderTarget(null)} />
  </ReaderChrome>;
}
