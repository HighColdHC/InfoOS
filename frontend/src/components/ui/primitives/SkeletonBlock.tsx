import "../../../styles/_helpers.css";
import { cx } from "../../../lib/format";

export interface SkeletonBlockProps {
  count?: number;
  label?: string;
  className?: string;
}

export function SkeletonBlock({ count = 3, label, className }: SkeletonBlockProps) {
  const safeCount = Math.max(0, Math.floor(count));
  const parentClass = cx("content-block-skeleton", className);
  return (
    <div className={parentClass} role="status" aria-label={label}>
      {Array.from({ length: safeCount }, (_, i) => (
        <div
          key={i}
          className={i === safeCount - 1 ? "skeleton-line short" : "skeleton-line"}
        />
      ))}
    </div>
  );
}
