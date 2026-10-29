import type { QueueTrackedTask } from "../../../../lib/types/sourceRuns";
import { dateTime, queueLabel, statusLabel, waitDuration } from "./sourceRunTraceViewModel";
import { partialAcquisitionLabel, stopReasonLabel } from "../../../../lib/acquisition";
import { TaskCancelButton } from "./TaskCancelButton";

export function TaskQueueTracking({
  tasks,
  title = "任务队列追踪",
  description = "采集任务及其后续任务的持久化队列状态。",
  emptyText = "暂无此来源运行的持久化队列追踪证据。",
  ariaLabel = "任务队列追踪",
  onTaskCancelled,
}: {
  tasks: QueueTrackedTask[];
  title?: string;
  description?: string;
  emptyText?: string;
  ariaLabel?: string;
  onTaskCancelled: () => void;
}) {
  if (!tasks.length)
    return (
      <section className="collector-task-queue-tracking" aria-label={ariaLabel}>
        <h3>{title}</h3>
        <p>{emptyText}</p>
      </section>
    );
  return (
    <section className="collector-task-queue-tracking" aria-label={ariaLabel}>
      <header>
        <h3>{title}</h3>
        <p>{description}</p>
      </header>
      <div className="collector-task-queue-table-scroll">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>队列 / 状态</th>
              <th>优先级</th>
              <th>等待 / 截止</th>
              <th>消费者</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.task_id} className={task.overdue ? "is-overdue" : undefined}>
                <td>
                  <strong>{task.task_type}</strong>
                  <code title={task.task_id}>{task.task_id}</code>
                  <TaskCancelButton taskId={task.task_id} status={task.status} onCancelled={onTaskCancelled} />
                </td>
                <td>
                  <strong>{queueLabel(task.queue_name)}</strong>
                  <small>{partialAcquisitionLabel(task) ?? statusLabel(task.status)}</small>
                  {task.completeness_status === "partial" && <small>{stopReasonLabel(task.stop_reason)}</small>}
                </td>
                <td>{task.priority}</td>
                <td>
                  <span>{waitDuration(task.wait_seconds)}</span>
                  <small>
                    {task.deadline_at ? `截止 ${dateTime(task.deadline_at)}` : "未设截止"}
                    {task.overdue ? " · 已逾期" : ""}
                  </small>
                </td>
                <td>{task.claimed_by ? <code title={task.claimed_by}>{task.claimed_by}</code> : "未认领"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
