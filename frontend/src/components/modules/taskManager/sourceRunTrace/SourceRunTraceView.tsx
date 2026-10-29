import { useState } from "react";
import { RunPipeline } from "./RunPipeline";
import type { Interruption } from "./sourceRunPipelineViewModel";
import { Button } from "../../../ui/primitives/Button";
import type { SourceRunTrace } from "../../../../lib/types/sourceRuns";
import { CardBuildSummary } from "./CardBuildSummary";
import { DiscoverySummary } from "./DiscoverySummary";
import { ItemCaptureTree } from "./ItemCaptureTree";
import { TaskQueueTracking } from "./TaskQueueTracking";
import { dateTime, duration, fact, statusLabel } from "./sourceRunTraceViewModel";
import { triggerLabel } from "../../../../lib/labels";
import { partialAcquisitionLabel, stopReasonLabel } from "../../../../lib/acquisition";

type Props = {
  trace: SourceRunTrace;
  blockingReason?: string | null;
  onCopy: (value: string, label: string) => void;
  onRetry: (taskId: string) => Promise<void>;
  onTaskCancelled: () => void;
  retryingTaskId?: string | null;
  onLoadMore?: () => void;
  loadingMore?: boolean;
};

export function SourceRunTraceView({ trace, onCopy, onRetry, retryingTaskId, onLoadMore, loadingMore, blockingReason, onTaskCancelled }: Props) {
  const { run } = trace;
  const [focused, setFocused] = useState<Interruption | null>(null);
  return (
    <div className="collector-task-trace">
      <section className="collector-task-trace-overview" aria-label="运行基本信息">
        <div>
          <span>运行 ID</span>
          <code>{run.source_run_id}</code>
          <button type="button" aria-label="复制运行 ID" onClick={() => onCopy(run.source_run_id, "运行 ID")}>
            复制
          </button>
        </div>
        <div>
          <span>根任务</span>
          <code>{run.root_task_id}</code>
          <button type="button" onClick={() => onCopy(run.root_task_id, "根任务 ID")}>
            复制
          </button>
        </div>
        <div>
          <span>来源</span>
          <b>{run.source_name}</b>
          <small>
            {run.platform} · {run.source_id}
          </small>
        </div>
        <div>
          <span>触发方式 / 状态</span>
          <b>
            {triggerLabel(run.trigger)} · {partialAcquisitionLabel(run) ?? statusLabel(run.status)}
          </b>
          {run.completeness_status === "partial" && <small>{stopReasonLabel(run.stop_reason)}</small>}
        </div>
        <div>
          <span>开始 / 耗时</span>
          <b>{dateTime(run.started_at)}</b>
          <small>{duration(run.duration_ms)}</small>
        </div>
        <div>
          <span>尝试次数</span>
          <b>{run.attempt_count}</b>
        </div>
      </section>
      <RunPipeline trace={trace} blockingReason={blockingReason} onLocate={(entry) => setFocused({ ...entry })} />
      <ItemCaptureTree upstreamInterrupted={run.execution_status === "failed" || run.execution_status === "blocked"} items={trace.items} focused={focused} onCopy={onCopy} onRetry={onRetry} retryingTaskId={retryingTaskId} onTaskCancelled={onTaskCancelled} />
      {trace.item_page.next_cursor && onLoadMore ? (
        <div className="collector-task-pagination">
          <span>逐条证据共 {trace.item_page.item_total} 条</span>
          <Button variant="secondary" size="sm" type="button" disabled={loadingMore} onClick={onLoadMore}>
            {loadingMore ? "正在加载…" : `加载更多条目（${trace.items.length} / ${trace.item_page.item_total}）`}
          </Button>
        </div>
      ) : null}
      <TaskQueueTracking tasks={trace.task_tracking} onTaskCancelled={onTaskCancelled} />
      <section aria-label="原始证据">
      {trace.events_archived ? <p role="note">较早的运行事件已归档，可在服务器用归档查询命令查看</p> : null}
      <section className="collector-task-trace-topstats" aria-label="采集统计与卡片进度">
        <div>
          <h3>采集统计</h3>
          <p>
            链接 {fact(run.fetch_stats.total_links)} · 去重 {fact(run.fetch_stats.dedup_skipped)} · 待抓取{" "}
            {fact(run.fetch_stats.pending_items)} · RawItem {fact(run.fetch_stats.raw_item_count)}
          </p>
        </div>
        <div>
          <h3>卡片进度</h3>
          <p>
            总数 {fact(run.card_progress.total)} · 完善 {fact(run.card_progress.complete)} · 部分{" "}
            {fact(run.card_progress.partial)} · 失败 {fact(run.card_progress.failed)}
          </p>
        </div>
      </section>
      {(trace.retired_steps.count > 0 || trace.retired_steps.availability !== "available") ? (
        <p style={{ color: "var(--color-text-muted)" }}>
          已退役步骤：{trace.retired_steps.count}
          {trace.retired_steps.availability !== "available" ? "（证据不完整，仅统计已读取任务）" : ""}
        </p>
      ) : null}
      <DiscoverySummary discovery={trace.discovery} onCopy={onCopy} />
      <CardBuildSummary stats={trace.card_build} />
      {trace.gaps.length ? (
        <details className="collector-task-gaps" aria-label="证据缺口">
          <summary>证据缺口</summary>
          <ul>
            {trace.gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </details>
      ) : null}
      </section>
    </div>
  );
}
