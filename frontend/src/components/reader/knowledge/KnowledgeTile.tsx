import type { KnowledgeListItem } from "../../../lib/types/knowledge";
import { KNOWLEDGE_LABELS, RESEARCH_LABELS } from "../../../lib/types/knowledge";
import { ATTRIBUTION, enumLabel } from "../../../lib/intelligenceLabels";
import { formatCardPreviewDate } from "../../modules/collector/cardPreview/cardPreviewDate";

export function KnowledgeTile({ item }: { item: KnowledgeListItem }) {
  return <a className="browse-reader__card knowledge-library__tile" href={item.deep_link}>
    <div className="browse-reader__card-body">
      <p className="card-reader__eyebrow">{KNOWLEDGE_LABELS[item.kind]} · {item.subtype_label}</p>
      <h2>{item.title}</h2>
      <p className="browse-reader__summary">{item.use_note}</p>
      <p className="browse-reader__relation">归属：{item.attribution_ref === null ? "暂无归属" : `${enumLabel(ATTRIBUTION, item.attribution_kind)} · ${item.attribution_ref}`}</p>
      <div className="knowledge-library__badges">
        <span className="badge badge--text">证据强度 {item.strength}</span>
        <span className="badge badge--text">证据 {item.evidence_count} 条</span>
        {item.has_conflict ? <span className="signal-chip signal-chip--warning">有冲突证据</span> : null}
        {item.kind === "opportunity" && item.research_status !== null ? <span className="signal-chip signal-chip--accent">{RESEARCH_LABELS[item.research_status]}</span> : null}
      </div>
      <p className="card-reader__meta">更新于 <time dateTime={item.updated_at}>{formatCardPreviewDate(item.updated_at)}</time></p>
    </div>
  </a>;
}
