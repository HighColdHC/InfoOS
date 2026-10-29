import type { SourceRunTrace } from "../../../../lib/types/sourceRuns";
import { dateTime, duration } from "./sourceRunTraceViewModel";
import { fullPipelineComplete, pipelineDuration, traceInterruptions, tracePipeline, stageState, type Interruption } from "./sourceRunPipelineViewModel";
import { PipelineSteps } from "./PipelineSteps";
import { Button } from "../../../ui/primitives/Button";

export function RunPipeline({ trace, blockingReason, onLocate }: {
  trace: SourceRunTrace; blockingReason?: string | null; onLocate: (entry: Interruption) => void;
}) {
  const interruptions = traceInterruptions(trace);
  const keys = [...new Set(interruptions.map((entry) => entry.key))];
  return <>
    <section className="collector-run-pipeline" aria-label="处理流程">
      <h3>处理流程</h3>
      <p>按持久化任务证据呈现 · 转录仅适用于有音轨的内容</p>
      {(trace.item_page.next_cursor || trace.item_page.truncated) && <p>逐篇聚合仅覆盖已加载 {trace.items.length}/{trace.item_page.item_total} 篇，请加载更多核对。</p>}
      <PipelineSteps steps={tracePipeline(trace)} />
    </section>
    {keys.map((key) => {
      const entries = interruptions.filter((entry) => entry.key === key);
      const affected = new Set(entries.map((entry) => entry.itemId).filter(Boolean)).size;
      const retryOnly = entries.every((entry) => stageState(entry.task) === "retrying");
      const heading = retryOnly ? `暂停于：${entries[0].label} · 待重试` : `中断于：${entries[0].label}`;
      return <section key={key} className={`collector-run-interruption${retryOnly ? " is-retrying" : ""}`} aria-label={heading}>
        <h3>{heading}</h3>
        <p>{key === "fetch" ? "影响本次运行 · 受影响篇数未知（抓取未完成）" : `影响已加载 ${affected} 篇`}</p>
        {entries.map((entry, index) => <div key={entry.task.task_id}>
          {entry.title && <strong>{entry.title}</strong>}
          <p>{entry.task.error?.summary ?? "错误摘要不可用"}</p>
          <small>尝试 {entry.task.attempt ?? "未知"}/{entry.task.max_attempts ?? "未知"} ·
            {entry.task.status === "pending" ? `等待重试 · 下次重试：${entry.task.retry_at ? dateTime(entry.task.retry_at) : "时间未知"}`
              : entry.task.status === "blocked" ? "已阻塞 · 未安排自动重试" : "失败 · 未安排自动重试"}</small>
          {entry.itemId && !entries.slice(0, index).some((previous) => previous.itemId === entry.itemId) && <Button type="button" variant="secondary" size="sm" onClick={() => onLocate(entry)}>定位到该篇</Button>}
        </div>)}
        {blockingReason && <p>阻塞原因：{blockingReason}</p>}
      </section>;
    })}
    {!interruptions.length && blockingReason && <section className="collector-run-interruption" aria-label="运行阻塞原因">
      <h3>运行被阻塞</h3><p>阻塞原因：{blockingReason}</p><p>具体中断步骤证据不可用</p>
    </section>}
    {!interruptions.length && !blockingReason && fullPipelineComplete(trace) && <p className="collector-run-complete">全流程完成 · 总耗时 {duration(pipelineDuration(trace))}</p>}
  </>;
}
