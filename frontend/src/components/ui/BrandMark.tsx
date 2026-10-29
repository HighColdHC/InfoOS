import { useId } from "react";

/** InfoOS mark: a signal emerging from concentric noise rings. */
export function BrandMark({ size = 30 }: { size?: number }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg className="brand-glyph" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id={`${id}-g`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8b7bff" />
          <stop offset="0.55" stopColor="#5468ff" />
          <stop offset="1" stopColor="#22c3ee" />
        </linearGradient>
        <radialGradient id={`${id}-c`} cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#fff" />
          <stop offset="1" stopColor="#d9d4ff" />
        </radialGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${id}-g)`} />
      <rect x="0.5" y="0.5" width="31" height="31" rx="8.5" fill="none" stroke="rgba(255,255,255,.35)" />
      <g fill="none" stroke="#fff" strokeLinecap="round">
        <path className="brand-glyph__outer" d="M16 6.5a9.5 9.5 0 1 1-8.2 4.7" strokeWidth="1.6" opacity=".55" />
        <path className="brand-glyph__inner" d="M16 11a5 5 0 1 0 4.6 3" strokeWidth="1.8" opacity=".85" />
      </g>
      <circle cx="16" cy="16" r="2.3" fill={`url(#${id}-c)`} />
    </svg>
  );
}
