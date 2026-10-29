import type { ContentBlock } from "../../lib/api";
import { ProblematicExplanation } from "../ui/primitives/ProblematicExplanation";
import { resolveMediaUrl } from "../../lib/mediaUrl";

export function TextBlock({
  block,
  isProblematic,
  isDepthLimited
}: {
  block: ContentBlock;
  isProblematic: boolean;
  isDepthLimited?: boolean;
}) {
  if (block.block_kind === "quote" || block.block_kind === "repost_original") {
    return <FramedTweetBlock block={block} />;
  }

  if (isProblematic) {
    return (
      <ProblematicExplanation
        status={block.status}
        blockKind={block.block_kind}
        fallback={block.content}
        variant="text"
      />
    );
  }

  return (
    <div className="content-block-text">
      {block.content || <em className="content-block-empty">No content available</em>}
    </div>
  );
}

function FramedTweetBlock({ block }: { block: ContentBlock }) {
  const author = block.raw_payload?.quoted_author ?? null;
  const at = block.raw_payload?.quoted_at ?? null;
  const assetPaths = block.raw_payload?.asset_paths ?? [];
  const mediaKind = block.raw_payload?.media_kind ? String(block.raw_payload.media_kind) : null;
  const metrics = block.raw_payload?.metrics ?? null;
  const isRepost = block.block_kind === "repost_original";
  const repostedBy = block.raw_payload?.reposted_by ?? null;
  const nestedQuote = block.raw_payload?.nested_quote ?? null;
  const label = isRepost ? "Reposting" : "Quoting";

  function isDepthLimited(b: ContentBlock | null): boolean {
    return !!b && b.status === "depth_limited";
  }

  return (
    <div className={`content-block content-block--${isRepost ? "repost" : "quote"}`}>
      <div className="quote-author-header">
        <span className="quote-action-label">{label}</span>
        {isRepost && repostedBy && (
          <span className="quote-reposted-by">
            {repostedBy.name ?? repostedBy.handle ?? "Unknown"} reposted
          </span>
        )}
        {author && (
          <>
            <span className="quote-author-name">{author.name ?? author.handle ?? "Unknown"}</span>
            {author.handle && author.url && (
              <a className="quote-author-handle" href={author.url} target="_blank" rel="noreferrer">
                @{author.handle}
              </a>
            )}
            {author.handle && !author.url && (
              <span className="quote-author-handle">@{author.handle}</span>
            )}
          </>
        )}
        {at && <span className="quote-author-time">{at.slice(0, 10)}</span>}
      </div>
      <div className="content-block-text">
        {block.content || <em className="content-block-empty">No content available</em>}
      </div>
      {(mediaKind || metrics) && (
        <div className="quote-meta-row">
          {mediaKind && <span className="quote-media-kind">{formatMediaKind(mediaKind)}</span>}
          {metricText(metrics, "reply_count", "replies")}
          {metricText(metrics, "retweet_count", "reposts")}
          {metricText(metrics, "like_count", "likes")}
        </div>
      )}
      {assetPaths.length > 0 && (
        <div className="quote-media-row">
          {assetPaths.map((path) => (
            <QuoteMediaAsset key={path} path={String(path)} />
          ))}
        </div>
      )}
      {nestedQuote && (
        <a className="content-block content-block--quote" href={nestedQuote.url ?? undefined} target="_blank" rel="noreferrer">
          <div className="quote-author-header">
            <span className="quote-action-label">Quoted post</span>
            {nestedQuote.author && (
              <span className="quote-author-name">
                {nestedQuote.author.name ?? nestedQuote.author.handle ?? "Unknown"}
              </span>
            )}
          </div>
          <div className="content-block-text">{nestedQuote.text || <em className="content-block-empty">No content available</em>}</div>
        </a>
      )}
      {isDepthLimited(isRepost ? null : block) && (
        <div className="depth-limited-note">📏 Quote depth limited to 1 level</div>
      )}
    </div>
  );
}

function QuoteMediaAsset({ path }: { path: string }) {
  const src = resolveMediaUrl(path);
  if (isVideoPath(path)) {
    return <video className="quote-media-video" src={src} controls preload="metadata" />;
  }
  return <img className="quote-media-image" src={src} alt="" loading="lazy" />;
}

function formatMediaKind(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return "";
  if (normalized === "video") return "Video";
  if (normalized === "photo" || normalized === "image") return "Image";
  return normalized[0].toUpperCase() + normalized.slice(1);
}

function isVideoPath(path: string): boolean {
  const value = path.toLowerCase().split("?")[0];
  return value.endsWith(".mp4") || value.endsWith(".mov") || value.endsWith(".webm");
}

function metricText(
  metrics: Record<string, number | string | null | undefined> | null,
  key: string,
  label: string,
) {
  if (!metrics) return null;
  const value = metrics[key];
  if (value === null || value === undefined || value === "") return null;
  const numberValue = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numberValue)) return null;
  return (
    <span className="quote-metric">
      {numberValue.toLocaleString("en-US")} {label}
    </span>
  );
}
