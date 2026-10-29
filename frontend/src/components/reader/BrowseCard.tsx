import type { CardPreviewListItem } from "../../lib/types/cardPreview";
import { ATTRIBUTION, enumLabel } from "../../lib/intelligenceLabels";
import { platformLabel } from "../../lib/platforms";
import { cardPreviewDateTime, formatCardPreviewDate } from "../modules/collector/cardPreview/cardPreviewDate";
import { PlatformMark } from "../ui/PlatformMark";
import { ScoreRing } from "../ui/ScoreRing";
import { cardReaderLinkProps } from "./cardReaderLink";

const NEXT_STEP = { verify: "待核实", observe: "继续观察", skip: "跳过" };

export function BrowseCard({ item, onOpen }: { item: CardPreviewListItem; onOpen: (cardId: string) => void }) {
  const evaluation = item.evaluation;
  const below = evaluation?.passed_quality === false;
  const link = cardReaderLinkProps(item.card_id, onOpen);
  return <article className={`browse-reader__card spotlight${below ? " browse-reader__card--below" : ""}${item.cover_asset_url ? " browse-reader__card--media" : ""}`}>
    {item.cover_asset_url && item.cover_kind !== "video" ? (
      <a className="browse-reader__cover" {...link} tabIndex={-1} aria-hidden="true">
        <img src={item.cover_asset_url} alt="" loading="lazy" />
      </a>
    ) : null}
    <div className="browse-reader__card-body">
      <p className="card-reader__meta browse-reader__card-meta">
        <PlatformMark platform={item.platform} size="sm" />
        <span>{item.author || "作者未记录"}</span><span>{platformLabel(item.platform)}</span>
        <time dateTime={cardPreviewDateTime(item.published_at)}>{item.published_at ? formatCardPreviewDate(item.published_at) : "发布时间未知"}</time></p>
      <h2><a {...link}>{item.title || "未命名信息卡"}</a></h2>
      {evaluation ? <>
        <p className="browse-reader__summary">{evaluation.summary || "暂无评估摘要"}</p>
        <p className="browse-reader__relation">与你的关系：{evaluation.relation_note || "未记录"}</p>
        <div className="browse-reader__badges">
          <span className="browse-reader__rings" aria-hidden="true">
            <ScoreRing value={evaluation.quality} label="质量" size={34} />
            <ScoreRing value={evaluation.relevance} label="相关" size={34} />
          </span>
          <span className="badge badge--text">质量 {evaluation.quality}</span>
          <span className="badge badge--text">相关 {evaluation.relevance ?? "—"}</span>
          {below ? <span className="signal-chip signal-chip--warning">未过线</span> : null}
          <span className="signal-chip signal-chip--accent">归属：{enumLabel(ATTRIBUTION, evaluation.attribution_kind)}</span>
        </div>
      </> : <p className="card-reader__state">尚未评估</p>}
      {item.opportunity?.has_breakdown ? <p className="browse-reader__opportunity"><span className="badge badge--text">商机</span> · AI 建议：{NEXT_STEP[item.opportunity.next_step]}</p> : null}
    </div>
  </article>;
}
