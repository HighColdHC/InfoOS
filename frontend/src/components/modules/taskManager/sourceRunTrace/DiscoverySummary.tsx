import type { SourceRunTrace } from "../../../../lib/types/sourceRuns";
import { fact } from "./sourceRunTraceViewModel";
import { TaskEvidencePanel } from "./TaskEvidencePanel";

export function DiscoverySummary({
  discovery,
  onCopy,
}: {
  discovery: SourceRunTrace["discovery"];
  onCopy: (value: string, label: string) => void;
}) {
  return (
    <section className="collector-task-trace-stage">
      <header>
        <div>
          <h3>OpenCLI 请求</h3>
          <p>链接发现与去重的持久化采集证据</p>
        </div>
      </header>
      <div className="collector-task-stat-row">
        <b>链接总数 {fact(discovery.total_links)}</b>
        <b>去重剔除 {fact(discovery.dedup_skipped)}</b>
        <b>需要抓取 {fact(discovery.pending_items)}</b>
      </div>
      {discovery.availability === "unavailable" ? (
        <p className="collector-task-unavailable">链接发现证据不可用</p>
      ) : null}
      <TaskEvidencePanel discovery={discovery} onCopy={onCopy} />
    </section>
  );
}
