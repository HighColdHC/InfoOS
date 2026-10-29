// Spinner — accessible loading indicator. Uses the design-token
// accent color (was hardcoded #4dabf7 in the legacy .loading-spinner
// class, which broke the warm rust palette). Pair with aria-live
// regions when the spinner signals a long-running operation.

import type { CSSProperties, ReactElement } from "react";

export type SpinnerProps = {
  size?: number;
  label?: string;
  tone?: "default" | "subtle";
  className?: string;
};

export function Spinner({ size = 32, label, tone = "default", className }: SpinnerProps): ReactElement {
  const style: CSSProperties = {
    width: `${size}px`,
    height: `${size}px`,
    borderWidth: `${Math.max(2, Math.round(size / 12))}px`,
  };
  const toneClass = tone === "subtle" ? "spinner subtle" : "spinner";
  return (
    <div
      role="status"
      aria-label={label ?? "加载中"}
      className={className ? `${toneClass} ${className}` : toneClass}
      style={style}
    />
  );
}
