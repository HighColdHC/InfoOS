import { Button } from "../../../ui/primitives/Button";
import { PlatformMark } from "../../../ui/PlatformMark";
import type { SourceRunExecutionBucket, SourceRunListItem } from "../../../../lib/types/sourceRuns";
import { dateTime, duration, fact, queueLabel, statusLabel, waitDuration } from "./sourceRunTraceViewModel";
import { triggerLabel } from "../../../../lib/labels";
import { listPipeline, stepStateLabels } from "./sourceRunPipelineViewModel";
import { partialAcquisitionLabel } from "../../../../lib/acquisition";

export function SourceRunTable({
  items,
  onTrace,
}: {
  items: SourceRunListItem[];
  onTrace: (run: SourceRunListItem) => void;
}) {
  return (
    <div className="collector-task-table-scroll">
      <table className="collector-task-table collector-source-run-table">
        <thead>
          <tr>
            <th>来源运行</th>
            <th>状态</th>
            <th>队列 / 优先级 / 等待</th>
            <th>采集 / 卡片</th>
            <th>计划 / 开始 / 耗时</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((run) => {
            const pipeline = listPipeline(run);
            const retryOnly = pipeline.states.includes("retrying") && !pipeline.states.some((state) => state === "failed" || state === "blocked");
            return (
              <tr key={run.source_run_id} onClick={(event) => {
                if ((event.target as HTMLElement).closest("button, a, input, select, textarea, [role=button], code")
                  || window.getSelection()?.toString()) return;
                onTrace(run);
              }}>
                <td>
                  <PlatformMark platform={run.platform} size="sm" withLabel />
                  <strong>{run.source_name}</strong>
                  <code title={run.source_run_id}>{run.source_run_id}</code>
                  <code title={run.source_id}>{run.source_id}</code>
                  {run.trigger && run.trigger !== "unavailable" && <small>{triggerLabel(run.trigger)}</small>}
                  <span className={`collector-task-execution-bucket is-${run.execution_bucket}`}>{executionBucketLabel(run.execution_bucket)}</span>
                </td>
                <td>
                  <span className={`collector-task-status is-${run.status}`}>{partialAcquisitionLabel(run) ?? statusLabel(run.status)}</span>
                  <div className="collector-run-mini" aria-label="抓取、入库、卡片进度">
                    {pipeline.states.map((state, index) => <span key={index} className={`is-${state}`}
                      title={stepStateLabels[state]}>{["抓取", "入库", "卡片"][index]}</span>)}
                  </div>
                  {pipeline.interruption && <small className={retryOnly ? "collector-run-retrying" : "collector-run-interrupted"}>
                    {retryOnly ? `暂停于 ${pipeline.interruption} · 待重试` : `中断于 ${pipeline.interruption}`}</small>}
                  {run.completeness_status === "partial" && <small>{run.stop_reason ?? "停止原因未知"}</small>}
                </td>
                <td className={run.overdue ? "is-overdue" : undefined}>
                  <strong>{queueLabel(run.queue_name)}</strong>
                  <small>
                    优先级 {run.priority ?? "—"} · 等待 {waitDuration(run.wait_seconds)}
                  </small>
                  <small>
                    {run.deadline_at ? `截止 ${dateTime(run.deadline_at)}` : "未设截止"}
                    {run.overdue ? " · 已逾期" : ""}
                    {run.claimed_by ? ` · ${run.claimed_by}` : ""}
                  </small>
                </td>
                <td>
                  链接 {fact(run.fetch_result.total_links)} · 去重 {fact(run.fetch_result.dedup_skipped)} · 待抓取{" "}
                  {fact(run.fetch_result.pending_items)}
                  <small>完善 {fact(run.card_progress.complete)} · 部分 {fact(run.card_progress.partial)} · 失败{" "}
                  {fact(run.card_progress.failed)}</small>
                </td>
                <td>
                  <span>计划：{dateTime(run.scheduled_at)}</span>
                  <small>
                    开始：{dateTime(run.started_at)} · 耗时：{duration(run.duration_ms)}
                  </small>
                </td>
                <td>
                  <Button
                    variant="secondary"
                    size="sm"
                    type="button"
                    aria-label={`追踪 ${run.source_name}`}
                    onClick={() => onTrace(run)}
                  >
                    追踪
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function executionBucketLabel(bucket: SourceRunExecutionBucket | undefined): string {
  const labels: Record<string, string> = { executed: "已执行", running: "正在执行", due: "已到期", future: "未来计划" };
  return bucket ? (labels[bucket] ?? bucket) : "未分类";
}
