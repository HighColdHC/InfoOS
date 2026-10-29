import type { CSSProperties } from "react";
import type { OpportunityItem, Stage } from "../../../lib/types/opportunities";
import { OpportunitySource } from "./OpportunitySource";
import { dateText } from "./shared";

export function OpportunityTile({ item, busy, onOpen, onMove, onDrag }: {
  item: OpportunityItem; busy: boolean; onOpen: () => void; onMove: (stage: Stage) => void; onDrag: (id: string | null) => void;
}) {
  const verification = item.latest_verification;
  const hint = item.stage === "inbox" ? `${item.question_count} 个问题待核实` : item.stage === "plan" ? "联系对方，问清交付与价格"
    : item.stage === "contact" ? (verification !== null && verification.doubts !== null ? verification.doubts : "持续观察，补充核实")
    : item.stage === "archive" ? (item.archive_reason === "dropped" ? "接触后放弃" : "暂不跟进，可随时恢复") : "已转入 PKM 行动";
  return <article className={`tile${item.stale ? " is-stale" : ""}`} style={{ "--stage": `var(--c-${item.stage})` } as CSSProperties}
    tabIndex={0} aria-label={item.offer} draggable={!busy} onClick={onOpen} onKeyDown={event => { if (event.key === "Enter") onOpen(); }}
    onDragStart={event => { event.dataTransfer.setData("text/plain", item.card_id); onDrag(item.card_id); event.currentTarget.classList.add("is-dragging"); }}
    onDragEnd={event => { onDrag(null); event.currentTarget.classList.remove("is-dragging"); }}>
    <div className="tile__top">{item.stage === "inbox" && Date.now() - Date.parse(item.created_at) < 86400000 ? <span className="chip chip--new">新</span> : null}
      <span className="chip chip--model">{item.model_label}</span>{item.stale ? <span className="chip chip--warn">待处理</span> : null}
      {item.candidate_kind === "case" ? <span className="chip chip--model">案例</span> : null}
      {item.risk_labels.map(risk => <span className="chip chip--warn" key={risk}>{risk}</span>)}
      </div>
    <OpportunitySource source={item} />
    <h3>{item.offer}</h3><p className="tile__next"><b>下一步：</b>{hint}</p>
    {item.stage === "inbox" ? <time className="tile__updated" dateTime={item.stage_since}>{dateText(item.stage_since)}</time> : null}
    <div className="tile__foot"><span className="tile__scores">复制 {item.replicability} · 可信 {item.credibility}</span>
    {item.stage === "inbox" ? <span className="tile__actions"><button className="btn btn--ghost" disabled={busy} onClick={event => { event.stopPropagation(); onMove("archive"); }}>暂不</button>
      <button className="btn btn--primary" disabled={busy} onClick={event => { event.stopPropagation(); onMove("plan"); }}>跟进</button></span> : <time dateTime={item.stage_since}>{dateText(item.stage_since)}</time>}</div>
  </article>;
}
