import type { SourceRunSummaryMetrics } from "../../../../lib/types/sourceRuns";

export function SourceRunMetrics({
  metrics,
  countsExact = true,
}: {
  metrics: SourceRunSummaryMetrics;
  countsExact?: boolean;
}) {
  const items = [
    ["最近 24 小时", metrics.last_24h],
    ["已完成", metrics.completed],
    ["需要关注", metrics.attention],
    ["排队 / 运行", metrics.active],
  ] as const;
  return (
    <div className="collector-task-metrics" aria-label="来源运行概览">
      {items.map(([label, value]) => (
        <article key={label}>
          <span>{countsExact ? label : `${label}（已扫描）`}</span>
          <strong>{countsExact ? value : `≥${value}`}</strong>
        </article>
      ))}
    </div>
  );
}
