import type { ContentBlock } from "../../lib/api";
import { ProblematicExplanation } from "../ui/primitives/ProblematicExplanation";
import { ExpandableText } from "../ui/primitives/ExpandableText";

export function LinkedArticleBlock({
  block,
  isProblematic
}: {
  block: ContentBlock;
  isProblematic: boolean;
}) {
  // Hide unsupported linked articles entirely
  if (block.status === "unsupported") return null;

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

  const content = block.content || "";

  return (
    <div className="content-block-text">
      {content ? (
        <ExpandableText text={content} charLimit={500} />
      ) : (
        <em className="content-block-empty">No article content available</em>
      )}
      {block.source_url && (
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
