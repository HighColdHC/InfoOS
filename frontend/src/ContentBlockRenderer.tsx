import "./styles/_content-blocks.css";
import "./styles/_media.css";
import "./styles/_ocr-transcript.css";
import { useState } from "react";
import { contentBlockPresentation } from "./lib/contentBlockLabels";
import type { ContentBlock } from "./lib/api";
import StatusBadge from "./StatusBadge";
import { TextBlock } from "./components/blocks/TextBlock";
import { LinkedArticleBlock } from "./components/blocks/LinkedArticleBlock";
import { ImageBlock } from "./components/blocks/ImageBlock";
import { VideoBlock } from "./components/blocks/VideoBlock";
import { MediaDerivedBlocks } from "./components/blocks/MediaDerivedBlocks";
import { SkeletonBlock } from "./components/ui/primitives/SkeletonBlock";

const layerClasses: Record<ContentBlock["layer"], string> = {
  original: "content-block-layer-original",
  media: "content-block-layer-media",
  context: "content-block-layer-context",
  derived: "content-block-layer-derived",
  intelligence: "content-block-layer-intelligence"
};

type ContentBlockRendererProps = {
  block: ContentBlock;
  relatedDerivedBlocks?: ContentBlock[];
};

export default function ContentBlockRenderer({ block, relatedDerivedBlocks }: ContentBlockRendererProps) {
  const [imageExpanded, setImageExpanded] = useState(false);
  const label = contentBlockPresentation(block.block_kind).label;
  const layerClass = layerClasses[block.layer];

  const isProblematicStatus =
    block.status === "blocked" ||
    block.status === "failed" ||
    block.status === "cleaned" ||
    block.status === "not_found" ||
    block.status === "unsupported";

  const isPending = block.status === "pending";
  const isDepthLimited = block.status === "depth_limited";

  const blockKindClass = `content-block-kind-${cssToken(block.block_kind)}`;
  const isMediaBlock = block.block_kind === "image" || block.block_kind === "video";

  return (
    <div className={`content-block ${layerClass} ${blockKindClass}`} data-block-kind={block.block_kind}>
      {label && (
        <div className="content-block-label">
          {label}
          {block.provenance && (
            <span className="content-block-provenance"> — {block.provenance}</span>
          )}
        </div>
      )}

      <StatusBadge status={block.status} />

      {isPending && <SkeletonBlock count={2} label="Loading content" />}

      {!isPending && block.block_kind === "image" && (
        <div className="media-block-container">
          <ImageBlock
            block={block}
            isProblematic={isProblematicStatus}
            expanded={imageExpanded}
            onToggle={() => setImageExpanded(!imageExpanded)}
          />
          <MediaDerivedBlocks
            block={block}
            relatedBlocks={relatedDerivedBlocks}
          />
        </div>
      )}

      {!isPending && block.block_kind === "video" && (
        <div className="media-block-container">
          <VideoBlock block={block} isProblematic={isProblematicStatus} />
          <MediaDerivedBlocks
            block={block}
            relatedBlocks={relatedDerivedBlocks}
          />
        </div>
      )}

      {!isPending && block.block_kind === "linked_article" && (
        <LinkedArticleBlock block={block} isProblematic={isProblematicStatus} />
      )}

      {!isPending &&
        block.block_kind !== "image" &&
        block.block_kind !== "video" &&
        block.block_kind !== "linked_article" && (
          <TextBlock
            block={block}
            isProblematic={isProblematicStatus}
            isDepthLimited={isDepthLimited}
          />
        )}

      {block.source_url && !isPending && block.block_kind !== "linked_article" && (
        <a
          href={block.source_url}
          target="_blank"
          rel="noreferrer"
          className="content-block-source"
        >
          {block.source_url}
        </a>
      )}
    </div>
  );
}

function cssToken(kind: string): string {
  const token = kind.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  return token || "unknown";
}
