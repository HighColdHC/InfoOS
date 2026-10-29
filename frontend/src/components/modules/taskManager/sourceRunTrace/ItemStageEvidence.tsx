import type { TraceStage } from "../../../../lib/types/sourceRuns";
import { dateTime } from "./sourceRunTraceViewModel";
import { stageLabel, stageState, stepStateLabels } from "./sourceRunPipelineViewModel";
import { TaskCancelButton } from "./TaskCancelButton";

export function ItemStageEvidence({ stages, focusedTaskId, onCopy, onRetry, retryingTaskId, onTaskCancelled }: {
  stages: TraceStage[]; focusedTaskId?: string; onCopy: (value: string, label: string) => void;
  onRetry: (taskId: string) => void; retryingTaskId?: string | null;
  onTaskCancelled: () => void;
}) {
  return <div className="collector-item-stages">{stages.map((stage) => {
    const state = stageState(stage);
    const interrupted = ["failed", "blocked", "retrying"].includes(state);
    return <div key={stage.task_id} data-task-id={stage.task_id} tabIndex={-1}
      className={`collector-item-stage is-${state}${focusedTaskId === stage.task_id ? " is-located" : ""}`}>
      <strong>{stageLabel(stage.stage)} · {stepStateLabels[state]}</strong>
      <small>尝试 {stage.attempt ?? "未知"}/{stage.max_attempts ?? "未知"}
        {stage.status === "pending" && stage.error && ` · 下次重试：${stage.retry_at ? dateTime(stage.retry_at) : "时间未知"}`}</small>
      {interrupted && <p>{stage.error?.summary ?? "错误摘要不可用"}</p>}
      <code>{stage.task_id}</code>
      <button type="button" onClick={() => onCopy(stage.task_id, "任务 ID")}>复制任务 ID</button>
      {interrupted && stage.status !== "pending" && (stage.attempt ?? 1) < (stage.max_attempts ?? 1) &&
        <button type="button" disabled={retryingTaskId === stage.task_id} onClick={() => onRetry(stage.task_id)}
          aria-label={`重试任务 ${stage.task_id}`}>{retryingTaskId === stage.task_id ? "提交中…" : "重试"}</button>}
      <TaskCancelButton taskId={stage.task_id} status={stage.status} onCancelled={onTaskCancelled} />
    </div>;
  })}</div>;
}
