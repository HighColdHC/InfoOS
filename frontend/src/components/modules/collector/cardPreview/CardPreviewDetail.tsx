import { BookOpen, ExternalLink, Info } from "lucide-react";
import { PlatformMark } from "../../../ui/PlatformMark";
import type { CardPreviewAsset, CardPreviewDetail as CardPreviewDetailType } from "../../../../lib/types/cardPreview";
import { CardContentSections } from "./CardContentSections";
import { cardPreviewDateTime, formatCardPreviewDate } from "./cardPreviewDate";
import { platformLabel } from "../../../../lib/platforms";
import { safeExternalUrl } from "../../../../lib/cardPresentation";
import { CompletenessBadge } from "./CompletenessBadge";
import { EngagementMetrics } from "./EngagementMetrics";

type Props = { detail: CardPreviewDetailType; onPreview: (assets: CardPreviewAsset[], index: number) => void; onRead: () => void };

export function CardPreviewDetail({ detail, onPreview, onRead }: Props) {
  const card = detail.card;
  const safeUrl = safeExternalUrl(card.source_url);
  return (
    <article className="collector-card-detail" aria-label="信息卡详情" key={card.card_id}>
      <header className="collector-card-detail__header">
        <div className="collector-card-detail__source-line">
          <span className="collector-card-detail__platform" title={platformLabel(card.platform)}>
            <PlatformMark platform={card.platform} size="lg" />
            <span className="sr-only">{platformLabel(card.platform)}</span>
          </span>
          <div>
            <strong>{card.author || card.source_label || "未知来源"}</strong>
            <span>{[card.source_label, formatCardPreviewDate(card.published_at)].filter(Boolean).join(" · ")}</span>
          </div>
          {card.completeness_status === "partial" ? <CompletenessBadge value={card.completeness_status} /> : null}
          <div className="collector-card-detail__links">
            <button type="button" className="collector-card-detail__source-link" onClick={onRead}>
              <BookOpen size={14} aria-hidden="true" /> 阅读
            </button>
            {safeUrl ? (
              <a className="collector-card-detail__source-link" href={safeUrl} target="_blank" rel="noopener noreferrer">
                打开来源 <ExternalLink size={14} aria-hidden="true" />
              </a>
            ) : null}
          </div>
        </div>
        <div className="collector-card-detail__identity">
          <h2>{card.title || "未命名信息卡"}</h2>
          {detail.missing_reasons.length ? (
            <div className="collector-card-detail__gaps" role="note">
              <strong>内容缺口：</strong>
              {detail.missing_reasons.join("；")}
            </div>
          ) : null}
        </div>
        <EngagementMetrics metrics={card.engagement_metrics} />
      </header>
      <CardContentSections detail={detail} onPreview={onPreview} />
      <footer className="collector-card-detail__footer">
        <details className="collector-card-detail__facts">
          <summary>
            <Info size={14} aria-hidden="true" />
            采集信息
          </summary>
          <dl>
            <div>
              <dt>卡片</dt>
              <dd>
                <code>{card.card_id}</code>
              </dd>
            </div>
            <div>
              <dt>发布时间</dt>
              <dd>
                <time dateTime={cardPreviewDateTime(card.published_at)}>
                  {formatCardPreviewDate(card.published_at)}
                </time>
              </dd>
            </div>
            <div>
              <dt>首次入库</dt>
              <dd>{formatCardPreviewDate(card.first_seen_at)}</dd>
            </div>
            <div>
              <dt>最近更新</dt>
              <dd>{formatCardPreviewDate(card.updated_at)}</dd>
            </div>
            <div>
              <dt>版本</dt>
              <dd>{versionLabel(card.version, card.content_hash)}</dd>
            </div>
            <div>
              <dt>处理器</dt>
              <dd>{card.processor_version || "未记录"}</dd>
            </div>
          </dl>
        </details>
        <span>{card.attachment_count} 个附件 · 只读预览</span>
      </footer>
    </article>
  );
}

function versionLabel(version: number, hash: string | null): string {
  if (!hash) return `v${version}`;
  return `v${version} · ${hash.length > 15 ? `${hash.slice(0, 14)}…` : hash}`;
}
