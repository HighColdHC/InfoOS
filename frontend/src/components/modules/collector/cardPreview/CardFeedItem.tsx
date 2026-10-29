import type { CardPreviewListItem } from "../../../../lib/types/cardPreview";
import { platformLabel } from "../../../../lib/platforms";
import { Play } from "lucide-react";
import { ACTION, ATTRIBUTION, CONTENT_TYPE, enumLabel } from "../../../../lib/intelligenceLabels";
import { PlatformMark } from "../../../ui/PlatformMark";
import { ScoreRing } from "../../../ui/ScoreRing";

function relativeTime(value: string | null | undefined): string {
  if (!value) return "时间未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未记录";
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    const hours = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 3_600_000));
    return `${hours} 小时前`;
  }
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

export function CardFeedItem({
  item,
  selected,
  onSelect,
}: {
  item: CardPreviewListItem;
  selected: boolean;
  onSelect: () => void;
}) {
  const processing = item.completeness_status === "processing";
  const evaluation = item.evaluation;
  return (
    <article
      className="card-feed-item"
      data-selected={selected || undefined}
      data-card-id={item.card_id}
      data-evaluation={evaluation ? (evaluation.passed_quality ? "passed" : "below") : "none"}
    >
      <button
        type="button"
        className="card-feed-item__button spotlight"
        onClick={onSelect}
        disabled={processing}
        aria-current={selected ? "true" : undefined}
      >
        <div className="card-feed-item__content">
          <div className="card-feed-item__meta">
            <PlatformMark platform={item.platform} size="sm" />
            {item.is_digest === true ? <span className="badge badge--text">精华</span> : null}
            <span className="card-feed-item__platform">{platformLabel(item.platform)}</span>
            <span className="card-feed-item__author">{item.author || item.source_label || "未知来源"}</span>
            <time dateTime={item.published_at || item.first_seen_at || undefined}>
              {relativeTime(item.published_at || item.first_seen_at)}
            </time>
          </div>
          <h3>{item.title || "未命名信息卡"}</h3>
          {item.excerpt && !evaluation?.passed_quality ? <p className="card-feed-item__excerpt">{item.excerpt}</p> : null}
          {evaluation ? evaluation.passed_quality ? (
            <div className="card-feed-item__evaluation">
              <p className="card-feed-item__evaluation-summary">{evaluation.summary}</p>
              <p className="card-feed-item__relation">与你的关系：{evaluation.relation_note}</p>
              <p className="card-feed-item__evaluation-meta">
                质量 {evaluation.quality} · 相关 {evaluation.relevance ?? "—"}
                {" · "}归属：{enumLabel(ATTRIBUTION, evaluation.attribution_kind)}
                {evaluation.attribution_ref ? ` · ${evaluation.attribution_ref}` : ""}
                {" · "}{enumLabel(CONTENT_TYPE, evaluation.content_type)}
                {" · "}AI 建议：{enumLabel(ACTION, evaluation.suggested_action)}
              </p>
            </div>
          ) : <small className="card-feed-item__not-recommended">未推荐 · 质量 {evaluation.quality}</small>
            : item.summary_excerpt ? <p className="card-feed-item__summary">AI：{item.summary_excerpt}</p> : null}
          {processing ? (
            <small className="card-feed-item__state">等待定稿</small>
          ) : item.completeness_status === "partial" ? (
            <small className="card-feed-item__state">部分缺失</small>
          ) : null}
        </div>
        {evaluation || item.cover_asset_url ? (
          <div className="card-feed-item__side">
            {evaluation ? <ScoreRing value={evaluation.quality} label="质量" size={36} /> : null}
            {item.cover_asset_url ? <FeedCover item={item} /> : null}
          </div>
        ) : null}
      </button>
    </article>
  );
}

function FeedCover({ item }: { item: CardPreviewListItem }) {
  const extra = (item.media_count || 0) - 1;
  return (
    <div className="card-feed-item__cover" data-kind={item.cover_kind ?? "image"}>
      {item.cover_kind === "video" ? (
        <>
          <video src={item.cover_asset_url ?? undefined} muted playsInline preload="metadata" aria-hidden="true" />
          <Play className="card-feed-item__play" size={18} fill="currentColor" aria-label="视频" />
        </>
      ) : (
        <img src={item.cover_asset_url ?? undefined} loading="lazy" alt="" />
      )}
      {extra > 0 ? <span>+{extra}</span> : null}
    </div>
  );
}
