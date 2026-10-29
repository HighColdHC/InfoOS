import type { ContentBlock } from "../../lib/api";
import { ProblematicExplanation } from "../ui/primitives/ProblematicExplanation";
import { isVideoUrl, resolveMediaUrl } from "../../lib/mediaUrl";

export function VideoBlock({ block, isProblematic }: { block: ContentBlock; isProblematic: boolean }) {
  if (isProblematic) {
    return (
      <ProblematicExplanation status={block.status} blockKind="video" variant="media" />
    );
  }
  if (!block.content) {
    return (
      <div className="media-placeholder">
        <div className="media-placeholder-icon">🎬</div>
        <p>Video not available</p>
      </div>
    );
  }

  const url = block.content.trim();
  const resolvedUrl = resolveMediaUrl(url);
  const isDirectVideo = isVideoUrl(resolvedUrl);

  if (!isDirectVideo) {
    return (
      <div className="media-placeholder">
        <div className="media-placeholder-icon">🎬</div>
        <p>Video reference: {url}</p>
      </div>
    );
  }

  return (
    <>
      <div className="media-caption">
        🎬 Video {block.provenance ? `• ${block.provenance}` : ""}
      </div>
      <video
        controls
        preload="metadata"
        className="content-block-video"
        aria-label={block.provenance || "Card video"}
      >
        <source src={resolvedUrl} />
        <p>Your browser does not support video playback.</p>
      </video>
    </>
  );
}
