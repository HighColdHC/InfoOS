import "../../styles/_card-reader.css";
import { isReaderDetail, ReaderBlock } from "./ReaderBlock";
import { safeExternalUrl } from "../../lib/cardPresentation";
import { platformLabel } from "../../lib/platforms";
import type { CardPreviewAsset, CardPreviewDetail } from "../../lib/types/cardPreview";
import { cardPreviewDateTime, formatCardPreviewDate } from "../modules/collector/cardPreview/cardPreviewDate";
import { ArrowUpRight } from "lucide-react";
import { PlatformMark } from "../ui/PlatformMark";
import { MediaGapNotice } from "../ui/MediaGapNotice";

export function ReaderContent({ detail }: { detail: CardPreviewDetail }) {
  const { card, body_blocks: bodyBlocks, comment_blocks: commentBlocks, image_assets: images, video_assets: videos } = detail;
  const readableBlocks = bodyBlocks.filter((block) => !isReaderDetail(block)).sort((a, b) =>
    readerPriority(a) - readerPriority(b));
  const detailBlocks = bodyBlocks.filter(isReaderDetail);
  const sourceUrl = safeExternalUrl(card.source_url);
  const stateLine = [
    card.completeness_status === "complete" ? "内容已完整" : card.completeness_status === "partial" ? "内容部分可用" : "内容处理中",
    ...(detail.missing_reasons.length ? ["部分内容尚未就绪"] : []),
  ].join(" · ");
  return (
    <article className="card-reader__article">
      <header className="card-reader__header">
        <p className="card-reader__eyebrow"><PlatformMark platform={card.platform} size="sm" />{platformLabel(card.platform)}</p>
        <h1>{card.title || "未命名信息卡"}</h1>
        <p className="card-reader__meta">
          {card.is_digest === true ? <span className="badge badge--text">精华</span> : null}
          <span>{card.author || card.source_label || "作者未记录"}</span>
          <span>发布于 <time dateTime={cardPreviewDateTime(card.published_at)}>{formatCardPreviewDate(card.published_at)}</time></span>
        </p>
        {sourceUrl ? <a className="card-reader__source" href={sourceUrl} target="_blank" rel="noopener noreferrer">查看原文<ArrowUpRight size={15} aria-hidden="true" /></a> : null}
        {detail.opportunity !== null ? <p className="card-reader__opportunity">商机拆解：{{ verify: "建议核实", observe: "持续观察", skip: "暂不考虑" }[detail.opportunity.next_step]} · <a href={`/r/opportunities?card=${encodeURIComponent(card.card_id)}`}>在商机看板打开</a></p> : null}
        <p className="card-reader__state">{stateLine}</p>
      </header>

      {detail.knowledge_cards.length ? <section aria-label="知识卡"><h2>从本卡拆出的知识卡</h2>
        <ul>{detail.knowledge_cards.map(knowledge => <li key={knowledge.knowledge_id}><a href={knowledge.deep_link}>{knowledge.title}</a></li>)}</ul>
      </section> : null}

      <div className="card-reader__blocks" aria-label="正文与内容块">
        {readableBlocks.length ? readableBlocks.map((block) => <ReaderBlock key={block.block_id} block={block} />) : <p className="card-reader__empty">暂无可读正文</p>}
      </div>

      {images.length || videos.length || detail.media_gap ? (
        <section className="card-reader__attachments" aria-label="附件">
          <h2>附件</h2>
          <MediaGapNotice gap={detail.media_gap} hasAttachments={images.length + videos.length > 0} sourceUrl={card.source_url} />
          {[...images, ...videos].map((asset) => <ReaderAsset key={asset.asset_id} asset={asset} />)}
        </section>
      ) : null}

      {commentBlocks.length ? (
        <section className="card-reader__comments" aria-label="评论">
          <h2>评论</h2>
          {commentBlocks.map((block) => <ReaderBlock key={block.block_id} block={block} />)}
        </section>
      ) : null}

      {detailBlocks.length ? (
        <details className="card-reader__details">
          <summary>处理细节</summary>
          {detailBlocks.map((block) => <ReaderBlock key={block.block_id} block={block} />)}
        </details>
      ) : null}
    </article>
  );
}

function readerPriority(block: CardPreviewDetail["body_blocks"][number]): number {
  return block.provenance === "intelligence.evaluate"
    ? block.block_kind === "intel_summary" ? -2 : block.block_kind === "relation_note" ? -1 : 0
    : 0;
}

function ReaderAsset({ asset }: { asset: CardPreviewAsset }) {
  const isVideo = asset.asset_kind === "video" || asset.mime_type?.startsWith("video/");
  return (
    <figure className="card-reader__asset">
      {isVideo ? (
        <video src={asset.asset_url} controls playsInline preload="metadata" aria-label="视频附件" />
      ) : (
        <img src={asset.asset_url} alt="卡片图片附件" loading="lazy" />
      )}
    </figure>
  );
}
