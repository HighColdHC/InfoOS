import type { ContentBlock } from "../../lib/api";
import { ProblematicExplanation } from "../ui/primitives/ProblematicExplanation";
import { isImageUrl, resolveMediaUrl } from "../../lib/mediaUrl";

export function ImageBlock({
  block,
  isProblematic,
  expanded,
  onToggle
}: {
  block: ContentBlock;
  isProblematic: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const imgContent = (block as unknown as Record<string, string>).body || block.content;
  if (isProblematic) {
    return (
      <ProblematicExplanation status={block.status} blockKind="image" variant="media" />
    );
  }
  if (!imgContent) {
    return (
      <div className="media-placeholder">
        <div className="media-placeholder-icon">🖼</div>
        <p>Image not available</p>
      </div>
    );
  }

  const url = imgContent.trim();
  const resolvedUrl = resolveMediaUrl(url);
  const isDirectImage = isImageUrl(resolvedUrl);

  if (!isDirectImage) {
    return (
      <div className="media-placeholder">
        <div className="media-placeholder-icon">🖼</div>
        <p>Image reference: {url}</p>
      </div>
    );
  }

  return (
    <>
      <button
        className="image-expand-button"
        type="button"
        onClick={onToggle}
        aria-label={expanded ? "Collapse image" : "Expand image"}
      >
        <img
          src={resolvedUrl}
          alt={block.provenance || "Card image"}
          className={`content-block-image ${expanded ? "expanded" : ""}`}
          loading="lazy"
          onError={(event) => {
            const target = event.currentTarget;
            target.style.display = "none";
            const placeholder = target.parentElement?.querySelector(".media-fallback");
            if (placeholder instanceof HTMLElement) placeholder.style.display = "block";
          }}
        />
        <div className="media-fallback" style={{ display: "none" }}>
          <div className="media-placeholder">
            <div className="media-placeholder-icon">🖼</div>
            <p>Image not available</p>
          </div>
        </div>
      </button>
      {expanded && (
        <div
          className="image-lightbox-backdrop"
          role="button"
          tabIndex={0}
          onClick={onToggle}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") onToggle();
          }}
          aria-label="Close image"
        >
          <img src={resolvedUrl} alt={block.provenance || "Card image"} className="image-lightbox-img" />
        </div>
      )}
    </>
  );
}
