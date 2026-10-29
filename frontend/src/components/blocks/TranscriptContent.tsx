import { ExpandableText } from "../ui/primitives/ExpandableText";

export function TranscriptContent({ content }: { content: string }) {
  if (!content.trim()) {
    return <em className="derived-empty-text">No transcript available</em>;
  }

  return (
    <div className="transcript-content">
      <ExpandableText text={content} charLimit={300} />
    </div>
  );
}
