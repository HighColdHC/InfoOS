import { useState } from "react";
import ContentBlockRenderer from "../../../../ContentBlockRenderer";
import type {
  CardPreviewAsset,
  CardPreviewDetail,
} from "../../../../lib/types/cardPreview";
import { MediaGallery } from "./MediaGallery";
import {
  bodyTranslationBlocks,
  interpretationBlocks,
  isOriginalBlock,
  isTranscriptBlock,
  transcriptTranslationFor,
} from "./cardPreviewPresentation";
import { SegmentedControl } from "../../../ui/primitives/SegmentedControl";
import { SignalMeter } from "../../../ui/ScoreRing";
import { ACTION, ATTRIBUTION, CONTENT_TYPE, enumLabel } from "../../../../lib/intelligenceLabels";
import { Sparkles } from "lucide-react";
import { MediaGapNotice } from "../../../ui/MediaGapNotice";

const languageOptions = [
  { label: "原文", value: "original" },
  { label: "中文翻译", value: "translation" },
] as const;

type Props = {
  detail: CardPreviewDetail;
  onPreview: (assets: CardPreviewAsset[], index: number) => void;
};

export function CardContentSections({ detail, onPreview }: Props) {
  const [bodyExpanded, setBodyExpanded] = useState(true);
  const [bodyLanguage, setBodyLanguage] = useState<"original" | "translation">("original");
  const transcriptBlocks = detail.body_blocks.filter(isTranscriptBlock);
  const summaryBlocks = interpretationBlocks(detail.body_blocks);
  const translationBlocks = bodyTranslationBlocks(detail.body_blocks);
  const evaluation = detail.evaluation;
  const showBodyLanguage = translationBlocks.length > 0;
  const readableBodyBlocks = detail.body_blocks.filter(isOriginalBlock);
  const displayedBodyBlocks = bodyLanguage === "translation" && showBodyLanguage ? translationBlocks : readableBodyBlocks;
  const mediaAssets = [...detail.image_assets, ...detail.video_assets];
  const commentBlocks = detail.comment_blocks;
  const commentCount = commentBlocks.reduce((total, block) => {
    const comments = block.raw_payload?.comments;
    return total + (Array.isArray(comments) ? comments.length : 1);
  }, 0);
  return (
    <div className="collector-card-sections">
      <section className="collector-card-section collector-card-eval" aria-label="评估" data-passed={evaluation ? String(evaluation.passed_quality) : undefined}>
        <header className="collector-card-section__heading">
          <h3><Sparkles size={14} aria-hidden="true" />评估</h3>
          {evaluation ? <span>{evaluation.passed_quality ? "已过线" : "未过线 · 沉底"}</span> : null}
        </header>
        {evaluation ? (
          <div className="card-eval">
            <div className="card-eval__meters">
              <SignalMeter label="质量" value={evaluation.quality} />
              <SignalMeter label="相关" value={evaluation.relevance} />
            </div>
            <div className="card-feed-item__evaluation">
              <p className="card-feed-item__evaluation-summary">{evaluation.summary}</p>
              <p className="card-feed-item__relation">与你的关系：{evaluation.relation_note}</p>
              <div className="card-eval__chips">
                <span className="signal-chip signal-chip--accent">归属 · {enumLabel(ATTRIBUTION, evaluation.attribution_kind)}{evaluation.attribution_ref ? ` · ${evaluation.attribution_ref}` : ""}</span>
                <span className="signal-chip">{enumLabel(CONTENT_TYPE, evaluation.content_type)}</span>
                <span className="signal-chip signal-chip--outline">AI 建议 · {enumLabel(ACTION, evaluation.suggested_action)}</span>
              </div>
              <p className="card-feed-item__evaluation-meta">
                质量 {evaluation.quality} · 相关 {evaluation.relevance ?? "—"}
                {" · "}rubric {evaluation.prompt_version} · 画像 {evaluation.profile_version}
                {" · "}evaluator {evaluation.model}
              </p>
            </div>
          </div>
        ) : <p className="collector-card-section__empty">尚未评估</p>}
      </section>
      {mediaAssets.length || detail.media_gap ? (
        <section className="collector-card-section" aria-label="媒体">
          <SectionHeading title="媒体" count={mediaAssets.length} suffix={mediaAssets.length ? "项 · 点击放大" : "项"} />
          <MediaGapNotice gap={detail.media_gap} hasAttachments={mediaAssets.length > 0} sourceUrl={detail.card.source_url} />
          {mediaAssets.length ? <MediaGallery assets={mediaAssets} onPreview={onPreview} /> : null}
        </section>
      ) : null}
      {summaryBlocks.length ? (
        <section className="collector-card-section" aria-label="历史摘要">
          <SectionHeading title="历史摘要" count={summaryBlocks.length} suffix="段" />
          <div className="collector-card-body-blocks">
            {summaryBlocks.map((block) => <ContentBlockRenderer key={block.block_id} block={block} />)}
          </div>
        </section>
      ) : null}
      <section className="collector-card-section" aria-label="原文">
        <header className="collector-card-section__heading">
          <h3>原始内容</h3>
          {showBodyLanguage ? (
            <SegmentedControl
              size="sm"
              label="正文语言"
              options={[...languageOptions]}
              value={bodyLanguage}
              onChange={setBodyLanguage}
            />
          ) : null}
        </header>
        <div className="collector-card-body-wrap" data-expanded={bodyExpanded || undefined}>
          <div className="collector-card-body-blocks">
            {displayedBodyBlocks.length ? (
              displayedBodyBlocks.map((block) => <ContentBlockRenderer key={block.block_id} block={block} />)
            ) : (
              <p className="collector-card-section__empty">暂无可读正文</p>
            )}
          </div>
          {needsExpansion(displayedBodyBlocks) ? (
            <button
              className="collector-card-body-toggle"
              type="button"
              onClick={() => setBodyExpanded((value) => !value)}
            >
              {bodyExpanded ? "收起正文" : "展开全文"}
            </button>
          ) : null}
        </div>
      </section>
      {transcriptBlocks.length ? (
        <details className="collector-card-section collector-card-section--fold" aria-label="转录块">
          <summary>
            视频转录 <span>{transcriptBlocks.length} 段</span>
          </summary>
          <div className="collector-card-body-blocks">
            {transcriptBlocks.map((block) => (
              <TranscriptBlock
                key={block.block_id}
                block={block}
                translation={transcriptTranslationFor(block, detail.body_blocks)}
              />
            ))}
          </div>
        </details>
      ) : null}
      {commentBlocks.length ? (
        <details className="collector-card-section collector-card-section--fold" aria-label="评论块">
          <summary>
            评论 <span>{commentCount} 条</span>
          </summary>
          <div className="collector-card-body-blocks">
            {commentBlocks.map((block) => (
              <ContentBlockRenderer key={block.block_id} block={block} />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function needsExpansion(blocks: CardPreviewDetail["body_blocks"]): boolean {
  return blocks.length > 1 || blocks.some((block) => String(block.content || "").length > 320);
}

function SectionHeading({ title, count, suffix }: { title: string; count: number; suffix: string }) {
  return (
    <header className="collector-card-section__heading">
      <h3>{title}</h3>
      <span>
        {count} {suffix}
      </span>
    </header>
  );
}

function TranscriptBlock({
  block,
  translation,
}: {
  block: CardPreviewDetail["body_blocks"][number];
  translation?: CardPreviewDetail["body_blocks"][number];
}) {
  const [language, setLanguage] = useState<"original" | "translation">("original");
  const displayedBlock = language === "translation" && translation ? translation : block;
  return (
    <div className="collector-transcript-block">
      {translation ? (
        <SegmentedControl
          size="sm"
          label="转录语言"
          options={[...languageOptions]}
          value={language}
          onChange={setLanguage}
        />
      ) : null}
      <ContentBlockRenderer block={displayedBlock} />
    </div>
  );
}
