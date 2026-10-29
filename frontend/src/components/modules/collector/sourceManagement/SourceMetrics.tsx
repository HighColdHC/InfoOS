import { Activity, AlertTriangle, CheckCircle2, Rss } from "lucide-react";
import type { SourceManagementSummary } from "../../../../lib/types/sourceManagement";
import { MetricGrid } from "../../../ui/MetricGrid";

export function SourceMetrics({ summary }: { summary: SourceManagementSummary }) {
  return (
    <MetricGrid metrics={[
      { label: "全部来源", value: summary.total_sources, icon: Rss },
      { label: "启用中", value: summary.enabled_sources, icon: CheckCircle2 },
      { label: "需要关注", value: summary.needs_attention, icon: AlertTriangle },
      { label: "近 7 天可靠性", value: `${(summary.reliability_7d * 100).toFixed(1)}%`, icon: Activity },
    ]} />
  );
}
