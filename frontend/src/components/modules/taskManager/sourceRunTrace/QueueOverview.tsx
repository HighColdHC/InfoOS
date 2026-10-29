import type { TaskQueueSummary } from "../../../../lib/types/sourceRuns";
import { queueLabel, waitDuration } from "./sourceRunTraceViewModel";

type Props = { queues: Record<string, TaskQueueSummary> };

export function QueueOverview({ queues }: Props) {
  const entries = Object.entries(queues);
  if (!entries.length) return null;
  return <section className="collector-task-queues" aria-label="队列概览">
    <div><h2>队列概览</h2><p>逾期表示超过任务截止时间，仍可由队列继续处理，不代表任务已自动失败。</p></div>
    <ul>{entries.map(([name, queue]) => {
      const workers = queue.running_worker_ids.join("、") || "暂无任务被认领";
      return <li key={name} className={queue.overdue ? "is-overdue" : undefined}>
        <div><strong>{queueLabel(name)}</strong><small>{name}</small></div>
        <span>待处理 {queue.pending} · 运行中 {queue.running}</span>
        <span>最久等待 {waitDuration(queue.oldest_wait_seconds)}</span>
        <span className={queue.overdue ? "collector-task-overdue" : undefined}>{queue.overdue ? `逾期 ${queue.overdue}` : "无逾期"}</span>
        <span aria-label={`当前认领 worker ${queue.running_worker_count} 个：${workers}`}>当前认领 worker {queue.running_worker_count}<small title={workers}>{workers}</small></span>
      </li>;
    })}</ul>
  </section>;
}
