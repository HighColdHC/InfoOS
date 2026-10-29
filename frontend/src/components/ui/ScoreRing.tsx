/** Circular 0–100 gauge; values at or above the threshold glow in the signal gradient. */
export function ScoreRing({ value, label, threshold = 60, size = 40 }: {
  value: number | null | undefined;
  label: string;
  threshold?: number;
  size?: number;
}) {
  const known = typeof value === "number" && Number.isFinite(value);
  const clamped = known ? Math.max(0, Math.min(100, value)) : 0;
  const radius = 15.5;
  const circumference = 2 * Math.PI * radius;
  const tone = !known ? "none" : clamped >= threshold ? "pass" : clamped >= threshold - 20 ? "near" : "low";
  return (
    <span className="score-ring" data-tone={tone} style={{ width: size, height: size }} role="img" aria-label={`${label} ${known ? clamped : "未知"}`} title={`${label} ${known ? clamped : "—"}`}>
      <svg viewBox="0 0 36 36" aria-hidden="true">
        <circle className="score-ring__track" cx="18" cy="18" r={radius} />
        <circle
          className="score-ring__value"
          cx="18"
          cy="18"
          r={radius}
          strokeDasharray={`${circumference} ${circumference}`}
          style={{ ["--ring-offset" as string]: String(circumference * (1 - clamped / 100)), ["--ring-full" as string]: String(circumference) }}
        />
      </svg>
      <span className="score-ring__number" aria-hidden="true">{known ? clamped : "—"}</span>
    </span>
  );
}

/** Horizontal 0–100 meter with a threshold tick, used where there is room for a label. */
export function SignalMeter({ value, label, threshold = 60 }: { value: number | null | undefined; label: string; threshold?: number }) {
  const known = typeof value === "number" && Number.isFinite(value);
  const clamped = known ? Math.max(0, Math.min(100, value)) : 0;
  const tone = !known ? "low" : clamped >= threshold ? "pass" : clamped >= threshold - 20 ? "near" : "low";
  return (
    <div className="signal-meter" data-tone={tone}>
      <div className="signal-meter__head">
        <span>{label}</span>
        <strong>{known ? clamped : "—"}</strong>
      </div>
      <div className="signal-meter__track" aria-hidden="true">
        <span className="signal-meter__fill" style={{ ["--value" as string]: `${clamped}%` }} />
        <span className="signal-meter__threshold" style={{ left: `${threshold}%` }} />
      </div>
    </div>
  );
}
