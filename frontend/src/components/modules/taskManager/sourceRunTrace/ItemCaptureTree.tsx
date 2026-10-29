import { Branch, ContentEnrichBranch } from "./ItemLegacyBranches";
import { ItemStageEvidence } from "./ItemStageEvidence";
import { PipelineSteps } from "./PipelineSteps";
import { isInterruption, itemPipeline, stageKey, stageState, type StepKey, type Interruption } from "./sourceRunPipelineViewModel";
import { Button } from "../../../ui/primitives/Button";
import { useEffect, useRef, useState } from "react";
import type {
  SourceRunTraceItem,
} from "../../../../lib/types/sourceRuns";
import { cardId, rawItemId } from "./sourceRunTraceViewModel";
import { runStatusLabel } from "../../../../lib/labels";

type Props = {
  items: SourceRunTraceItem[];
  upstreamInterrupted?: boolean;
  focused?: Interruption | null;
  onCopy: (value: string, label: string) => void;
  onRetry: (taskId: string) => Promise<void>;
  onTaskCancelled: () => void;
  retryingTaskId?: string | null;
};

export function ItemCaptureTree({ items, onCopy, onRetry, retryingTaskId, focused, upstreamInterrupted = false, onTaskCancelled }: Props) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(items.filter((item, index) => index < 2 || itemPipeline(item).some((s) => isInterruption(s.state))).map(rawItemId)));
  const [stageVisibility, setStageVisibility] = useState<Record<string, boolean>>({});
  const tree = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!focused?.itemId) return;
    setOpen((current) => new Set([...current, focused.itemId!]));
    setStageVisibility((current) => {
      const next = { ...current };
      delete next[`${focused.itemId}:${focused.key}`];
      return next;
    });
    const frame = requestAnimationFrame(() => {
      const article = [...(tree.current?.querySelectorAll<HTMLElement>("[data-item-id]") ?? [])]
        .find((node) => node.dataset.itemId === focused.itemId);
      const node = [...(article?.querySelectorAll<HTMLElement>("[data-task-id]") ?? [])]
        .find((node) => node.dataset.taskId === focused.task.task_id) ?? article;
      node?.focus({ preventScroll: true });
      node?.scrollIntoView({ block: "center", behavior: "auto" });
    });
    return () => cancelAnimationFrame(frame);
  }, [focused]);
  const [confirmTaskId, setConfirmTaskId] = useState<string | null>(null);
  useEffect(() => setOpen(new Set(items.filter((item, index) => index < 2 || itemPipeline(item).some((s) => isInterruption(s.state))).map(rawItemId))), [items]);
  function toggle(id: string) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  return (
    <section ref={tree} className="collector-task-trace-stage">
      <header>
        <div>
          <h3>逐篇处理</h3>
          <p>正文与媒体处理分支按每篇信息卡独立呈现</p>
        </div>
      </header>
      <div className="collector-task-items">
        {items.map((item) => {
          const rawId = rawItemId(item);
          const expanded = open.has(rawId);
          const steps = itemPipeline(item, upstreamInterrupted);
          const expandedSteps = new Set(steps.filter((step) => expanded &&
            (stageVisibility[`${rawId}:${step.key}`] ?? isInterruption(step.state))).map((step) => step.key));
          function toggleStage(key: StepKey) {
            setOpen((current) => new Set([...current, rawId]));
            setStageVisibility((current) => ({ ...current, [`${rawId}:${key}`]: !expandedSteps.has(key) }));
          }
          return (
            <article key={rawId} data-item-id={rawId} tabIndex={-1}
              className={`collector-task-item${focused?.itemId === rawId ? " is-located" : ""}`}>
              <button
                className="collector-task-item-toggle"
                type="button"
                aria-expanded={expanded}
                aria-label={`${expanded ? "收起" : "展开"}${item.title} 抓取树`}
                onClick={() => toggle(rawId)}
              >
                <span>
                  <strong>{item.title}</strong>
                  <small>
                    {rawId} · {cardId(item)}
                  </small>
                </span>
                <b>{runStatusLabel(item.finalize.completeness)}</b>
              </button>
              <PipelineSteps steps={steps} mini expandedSteps={expandedSteps} onToggle={toggleStage} />
              {expanded ? (
                <div role="region" aria-label={`${item.title} 抓取树`} className="collector-task-item-tree">
                  {item.stages?.length ? <ItemStageEvidence stages={item.stages.filter((stage) =>
                    (expandedSteps.has(stageKey(stage.stage) as StepKey) &&
                      (stageVisibility[`${rawId}:${stageKey(stage.stage)}`] === true || isInterruption(stageState(stage))))
                    || !steps.some((step) => step.key === stageKey(stage.stage)))} focusedTaskId={focused?.task.task_id}
                    onCopy={onCopy} onRetry={setConfirmTaskId} retryingTaskId={retryingTaskId} onTaskCancelled={onTaskCancelled} /> : <>
                  <ContentEnrichBranch
                    evidence={item.content_enrich}
                    onTaskCancelled={onTaskCancelled}
                    onRetry={setConfirmTaskId}
                    retryingTaskId={retryingTaskId}
                  />
                  <Branch
                    label="图片下载"
                    onTaskCancelled={onTaskCancelled}
                    branch={item.image_download}
                    onRetry={setConfirmTaskId}
                    retryingTaskId={retryingTaskId}
                  />
                  <Branch
                    label="视频下载"
                    onTaskCancelled={onTaskCancelled}
                    branch={item.video_download}
                    onRetry={setConfirmTaskId}
                    retryingTaskId={retryingTaskId}
                  />
                  <Branch
                    label="视频转录"
                    onTaskCancelled={onTaskCancelled}
                    branch={item.video_transcription}
                    onRetry={setConfirmTaskId}
                    retryingTaskId={retryingTaskId}
                  />
                  <div className="collector-task-finalize">
                    <strong>信息卡定稿</strong>
                    <span>
                      {runStatusLabel(item.finalize.status)} · 完整度 {runStatusLabel(item.finalize.completeness)} ·
                      版本 {item.finalize.version ?? "不可用"}
                    </span>
                    <code>{item.finalize.task_id}</code>
                    <button type="button" onClick={() => onCopy(item.finalize.task_id, "任务 ID")}>
                      复制
                    </button>
                  </div>
                  </>}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
      {confirmTaskId ? (
        <div className="collector-task-confirm" role="alertdialog" aria-label="确认重试失败分支" aria-modal="true">
          <div>
            <h4>创建新的失败分支尝试？</h4>
            <p>原失败任务和事件证据会保留。本次只重试：</p>
            <code>{confirmTaskId}</code>
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={() => setConfirmTaskId(null)}>
                取消
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => {
                  const id = confirmTaskId;
                  setConfirmTaskId(null);
                  void onRetry(id);
                }}
              >
                确认创建新尝试
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
