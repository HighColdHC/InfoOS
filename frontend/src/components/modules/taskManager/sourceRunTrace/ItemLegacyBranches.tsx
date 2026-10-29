import type { ContentEnrichEvidence, MediaBranch } from "../../../../lib/types/sourceRuns";
import { branchSummary } from "./sourceRunTraceViewModel";
import { runStatusLabel } from "../../../../lib/labels";
import { TaskCancelButton } from "./TaskCancelButton";

export function Branch({
  label,
  branch,
  onRetry,
  retryingTaskId,
  onTaskCancelled,
}: {
  label: string;
  branch: MediaBranch;
  onRetry: (taskId: string) => void;
  retryingTaskId?: string | null;
  onTaskCancelled: () => void;
}) {
  const tasks = branch.tasks;
  return (
    <div className="collector-task-branch">
      <div>
        <strong>{label}</strong>
        <span>{branchSummary(branch)}</span>
      </div>
      {tasks
        .filter((task) => task.status === "failed" || task.status === "blocked" || task.status === "pending")
        .map((task) => (
          <div className="collector-task-failure" key={task.task_id}>
            <code>{task.task_id}</code>
            <span>{task.error_summary ?? runStatusLabel(task.status)}</span>
            {task.retryable ? (
              <button
                disabled={retryingTaskId === task.task_id}
                type="button"
                aria-label={`重试任务 ${task.task_id}`}
                onClick={() => onRetry(task.task_id)}
              >
                {retryingTaskId === task.task_id ? "提交中…" : "重试"}
              </button>
            ) : (
              <small>不可重试</small>
            )}
            <TaskCancelButton taskId={task.task_id} status={task.status} onCancelled={onTaskCancelled} />
          </div>
        ))}
    </div>
  );
}

export function ContentEnrichBranch({
  evidence,
  onRetry,
  retryingTaskId,
  onTaskCancelled,
}: {
  evidence: ContentEnrichEvidence;
  onRetry: (taskId: string) => void;
  retryingTaskId?: string | null;
  onTaskCancelled: () => void;
}) {
  if (evidence.availability === "unavailable")
    return (
      <div className="collector-task-branch">
        <div>
          <strong>正文富化</strong>
          <span className="collector-task-unavailable">正文处理证据不可用</span>
        </div>
      </div>
    );
  const attempts =
    evidence.attempt === null ? "尝试次数不可用" : `第 ${evidence.attempt}/${evidence.max_attempts ?? "?"} 次尝试`;
  return (
    <div className="collector-task-branch">
      <div>
        <strong>正文处理</strong>
        <span>
          {runStatusLabel(evidence.status)} · {attempts}
        </span>
      </div>
      <div className="collector-task-failure">
        <code>{evidence.task_id}</code>
        <span>{evidence.error ?? "正文处理任务证据"}</span>
        {evidence.retryable ? (
          <button
            disabled={retryingTaskId === evidence.task_id}
            type="button"
            aria-label={`重试任务 ${evidence.task_id}`}
            onClick={() => onRetry(evidence.task_id)}
          >
            {retryingTaskId === evidence.task_id ? "提交中…" : "重试"}
          </button>
        ) : (
          <small>不可重试</small>
        )}
        <TaskCancelButton taskId={evidence.task_id} status={evidence.status} onCancelled={onTaskCancelled} />
      </div>
    </div>
  );
}
