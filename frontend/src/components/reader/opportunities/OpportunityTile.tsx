import type { CSSProperties } from "react";
import type { BoardColumn, OpportunityItem, ResearchStatus } from "../../../lib/types/opportunities";
import { platformLabel } from "../../../lib/platforms";
import { dateText, SUBTYPES } from "./shared";

export function OpportunityTile({ item, columns, busy, onMove, onDrag }: {
  item: OpportunityItem; columns: BoardColumn[]; busy: boolean;
  onMove: (status: ResearchStatus) => void; onDrag: (id: string | null) => void;
}) {
  return <article className="tile" style={{ "--stage": `var(--c-${item.research_status})` } as CSSProperties}
    aria-label={item.title} draggable={!busy} tabIndex={0}
    onClick={() => window.location.assign(`/r/knowledge/${encodeURIComponent(item.knowledge_id)}`)}
    onKeyDown={event => { if (event.key === "Enter" && event.target === event.currentTarget) window.location.assign(`/r/knowledge/${encodeURIComponent(item.knowledge_id)}`); }}
    onDragStart={event => { event.dataTransfer.setData("text/plain", item.knowledge_id); onDrag(item.knowledge_id); event.currentTarget.classList.add("is-dragging"); }}
    onDragEnd={event => { onDrag(null); event.currentTarget.classList.remove("is-dragging"); }}>
    <div className="tile__top"><span className="chip chip--model">{SUBTYPES[item.subtype]}</span><span>强度 {item.strength}</span></div>
    <h3><a href={`/r/knowledge/${encodeURIComponent(item.knowledge_id)}`}>{item.title}</a></h3>
    <p className="tile__next">{item.money_signal}</p>
    <div className="tile__top"><span>证据 {item.evidence_count}</span>{item.platforms.map(platform => <span key={platform}>{platformLabel(platform)}</span>)}</div>
    <div className="tile__foot"><time dateTime={item.status_changed_at}>{dateText(item.status_changed_at)}</time>
      <select onClick={event => event.stopPropagation()} aria-label={`${item.title}的调研状态`} value={item.research_status} disabled={busy}
        onChange={event => onMove(event.target.value as ResearchStatus)}>
        {columns.map(column => <option key={column.status} value={column.status}>{column.label}</option>)}
      </select></div>
  </article>;
}
