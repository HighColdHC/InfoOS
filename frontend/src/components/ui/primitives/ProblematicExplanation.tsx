import { getStatusExplanation } from "../../../lib/statusExplanation";

export interface ProblematicExplanationProps {
  status: string | null | undefined;
  blockKind: string;
  fallback?: string | null | undefined;
  variant?: "text" | "media";
  className?: string;
}

export function ProblematicExplanation({
  status,
  blockKind,
  fallback,
  variant = "text",
  className
}: ProblematicExplanationProps) {
  if (!status) return null;
  const explanation = getStatusExplanation(status, blockKind);
  if (variant === "media") {
    return (
      <div className={className ?? "media-placeholder"}>
        <div className="media-placeholder-icon">⚠</div>
        <p>{explanation}</p>
        {fallback && <p className="media-fallback">{fallback}</p>}
      </div>
    );
  }
  return (
    <div className={className ?? "content-block-explanation"}>
      <p>{explanation}</p>
      {fallback && <p className="content-block-fallback">{fallback}</p>}
    </div>
  );
}
