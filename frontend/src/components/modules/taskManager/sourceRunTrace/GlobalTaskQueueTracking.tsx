import { useEffect, useState } from "react";
import { fetchTaskTrackingPage } from "../../../../lib/api/sourceRuns";
import { taskQueueNames, type TaskTrackingPage } from "../../../../lib/types/sourceRuns";
import { Button } from "../../../ui/primitives/Button";
import { TaskQueueTracking } from "./TaskQueueTracking";
import { queueLabel } from "./sourceRunTraceViewModel";

const PAGE_SIZE = 20;
const STATUS_OPTIONS = [
  ["", "全部状态"],
  ["pending", "等待中"],
  ["running", "执行中"],
  ["failed", "失败"],
  ["blocked", "已阻塞"],
  ["success", "成功"],
  ["skipped", "已跳过"],
] as const;

export function GlobalTaskQueueTracking({ refreshKey }: { refreshKey: number }) {
  const [page, setPage] = useState<TaskTrackingPage | null>(null);
  const [queueName, setQueueName] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelRefresh, setCancelRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setError(null);
    void fetchTaskTrackingPage({ limit: PAGE_SIZE, offset, queueName, status }, controller.signal)
      .then((result) => {
        if (current) setPage(result);
      })
      .catch((reason: unknown) => {
        if (current && !(reason instanceof DOMException && reason.name === "AbortError")) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [offset, queueName, status, refreshKey, cancelRefresh]);

  return (
    <section className="collector-task-panel" aria-label="全部持久任务">
      <div className="collector-task-heading">
        <div>
          <h2>全部持久任务</h2>
          <p>包含来源链、维护、导出及其他未关联 Source Run 的任务。</p>
        </div>
      </div>
      <div className="collector-task-filters">
        <label>
          <span>队列</span>
          <select
            aria-label="全局任务队列筛选"
            value={queueName}
            onChange={(event) => {
              setQueueName(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部队列</option>
            {taskQueueNames.map((queue) => (
              <option value={queue} key={queue}>
                {queueLabel(queue)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>状态</span>
          <select
            aria-label="全局任务状态筛选"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            {STATUS_OPTIONS.map(([value, label]) => (
              <option value={value} key={value || "all"}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {loading && !page ? (
        <div className="collector-task-state" role="status">
          正在读取全部持久任务…
        </div>
      ) : null}
      {error ? (
        <div className="collector-task-state is-error" role="alert">
          任务读取失败：{error}
        </div>
      ) : null}
      {page ? (
        <TaskQueueTracking
          tasks={page.items}
          onTaskCancelled={() => setCancelRefresh((value) => value + 1)}
          title="队列任务明细"
          description="按持久化队列查看每个任务的时效、状态和认领者。"
          emptyText="当前筛选下没有持久任务。"
          ariaLabel="全部任务队列追踪"
        />
      ) : null}
      {page ? (
        <div className="collector-task-pagination">
          <span>
            共 {page.total} 条 · 第 {Math.floor(offset / PAGE_SIZE) + 1} 页
          </span>
          <div>
            <Button
              size="sm"
              aria-label="全部任务上一页"
              disabled={offset === 0 || loading}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              上一页
            </Button>
            <Button
              size="sm"
              aria-label="全部任务下一页"
              disabled={page.next_offset === null || loading}
              onClick={() => setOffset(page.next_offset ?? offset)}
            >
              下一页
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
