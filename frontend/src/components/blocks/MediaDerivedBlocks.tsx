import { contentBlockPresentation } from "../../lib/contentBlockLabels";
import type { ContentBlock } from "../../lib/api";
import { TranscriptContent } from "./TranscriptContent";
import { SkeletonBlock } from "../ui/primitives/SkeletonBlock";

export function MediaDerivedBlocks({
  block,
  relatedBlocks
}: {
  block: ContentBlock;
  relatedBlocks?: ContentBlock[];
}) {
  const isImage = block.block_kind === "image";
  const isVideo = block.block_kind === "video";

  // If we have related derived blocks, render them
  if (relatedBlocks && relatedBlocks.length > 0) {
    return (
      <div className="media-derived-section">
        {relatedBlocks.map((derivedBlock) => (
          <DerivedBlockRenderer key={derivedBlock.block_id} block={derivedBlock} />
        ))}
      </div>
    );
  }

  // No derived blocks yet — show pending note
  return (
    <div className="media-pending-note">
      {isImage && "Media understanding pending — OCR and description will appear here"}
      {isVideo && "Video transcript pending — audio will be transcribed"}
    </div>
  );
}

export function DerivedBlockRenderer({ block }: { block: ContentBlock }) {
  const isPending = block.status === "pending";
  const isFailed = block.status === "failed";

  if (block.block_kind === "image_ocr") {
    return (
      <div className="ocr-text">
        <div className="derived-block-label">⧩ {contentBlockPresentation(block.block_kind).label}</div>
        {isPending ? (
          <>
            <SkeletonBlock count={2} label="Extracting text" />
            <span className="derived-pending-text">Extracting text…</span>
          </>
        ) : !block.content || block.content.trim().length === 0 ? (
          <em className="derived-empty-text">No text detected in this image</em>
        ) : (
          <pre className="ocr-text-content">{block.content}</pre>
        )}
      </div>
    );
  }

  if (block.block_kind === "image_description") {
    return (
      <div className="image-description">
        <div className="derived-block-label">⧩ {contentBlockPresentation(block.block_kind).label}</div>
        {isPending ? (
          <>
            <SkeletonBlock count={2} label="Generating description" />
            <span className="derived-pending-text">Generating description…</span>
          </>
        ) : (
          <p className="image-description-content">{block.content || "No description available"}</p>
        )}
      </div>
    );
  }

  if (block.block_kind === "video_transcript") {
    return (
      <div className="video-transcript">
        <div className="derived-block-label">⧩ {contentBlockPresentation(block.block_kind).label}</div>
        {isPending ? (
          <>
            <SkeletonBlock count={2} label="Transcribing audio" />
            <span className="derived-pending-text">Transcribing audio…</span>
          </>
        ) : isFailed ? (
          <div className="transcript-failed">
            <strong>Transcription failed</strong>
            <p>Audio could not be transcribed. The video may not have audible speech, or the transcription service may be unavailable.</p>
          </div>
        ) : (
          <TranscriptContent content={block.content || ""} />
        )}
      </div>
    );
  }

  return null;
}
