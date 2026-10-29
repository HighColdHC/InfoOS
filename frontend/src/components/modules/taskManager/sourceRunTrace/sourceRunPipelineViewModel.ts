import type { MediaBranch, SourceRunListItem, SourceRunTrace, SourceRunTraceItem, TraceStage } from "../../../../lib/types/sourceRuns";
import { rawItemId } from "./sourceRunTraceViewModel";

export const pipelineSteps = [
  ["queue", "排队"], ["fetch", "抓取"], ["discovery", "发现去重"],
  ["card_build", "卡片构建"], ["content_enrich", "正文富化"],
  ["media_download", "媒体下载"], ["video_transcribe", "转录"], ["card_finalize", "定稿"],
] as const;
export type StepKey = typeof pipelineSteps[number][0];
export type StepState = "complete" | "running" | "pending" | "retrying" | "not_executed" | "failed" | "blocked" | "skipped" | "na" | "unknown";
export type PipelineStep = { key: StepKey; label: string; state: StepState; complete: number; total: number; failed: number };
export type Interruption = { key: StepKey; label: string; itemId?: string; title?: string; task: TraceStage };
export const stepStateLabels: Record<StepState, string> = {
  complete: "完成", running: "进行中", pending: "排队", failed: "失败", blocked: "阻塞",
  retrying: "待重试", not_executed: "未执行", skipped: "跳过", na: "不适用", unknown: "证据不可用",
};
export function stageLabel(stage: string): string {
  return pipelineSteps.find(([key]) => key === stage.toLowerCase())?.[1]
    ?? ({ video_extract_audio: "提取音轨", webhook_deliver: "Webhook 投递" }[stage.toLowerCase()] ?? stage);
}
export function stageState(task: Pick<TraceStage, "status" | "error">): StepState {
  if (task.status === "pending" && task.error) return "retrying";
  return ({ success: "complete", succeeded: "complete", complete: "complete", partial: "complete",
    running: "running", pending: "pending", failed: "failed", blocked: "blocked", skipped: "skipped",
  } as Record<string, StepState>)[task.status] ?? "unknown";
}
function combine(states: StepState[]): StepState {
  return (["blocked", "failed", "retrying", "running", "pending", "unknown", "complete", "skipped", "na", "not_executed"] as StepState[])
    .find((state) => states.includes(state)) ?? "unknown";
}
function branchState(branch: MediaBranch): StepState {
  const tasks = branch.tasks;
  if (tasks.length) return combine(tasks.map((task) => stageState({ status: task.status,
    error: task.error_summary ? { class: "task_error", summary: task.error_summary } : null })));
  if (branch.availability === "unavailable" || branch.total === null) return "unknown";
  if (branch.total === 0) return "na";
  if (branch.failed) return "failed";
  return branch.success === branch.total ? "complete" : "pending";
}
export function stageKey(stage: string): string {
  return stage.toLowerCase() === "video_extract_audio" ? "video_transcribe" : stage.toLowerCase();
}
export function isInterruption(state: StepState): boolean {
  return ["failed", "blocked", "retrying"].includes(state);
}
function hasItemEvidence(item: SourceRunTraceItem, key: StepKey): boolean {
  if (item.stages?.some((task) => stageKey(task.stage) === key)) return true;
  const validId = (id: string) => Boolean(id && id !== "unavailable");
  if (key === "content_enrich") return validId(item.content_enrich.task_id);
  if (key === "card_finalize") return validId(item.finalize.task_id);
  const branches = key === "media_download" ? [item.image_download, item.video_download]
    : key === "video_transcribe" ? [item.video_transcription] : [];
  return branches.some((branch) => branch.tasks.length > 0 || (branch.total ?? 0) > 0);
}
export function itemPipeline(item: SourceRunTraceItem, upstreamInterrupted = false): PipelineStep[] {
  let stopped = upstreamInterrupted;
  return pipelineSteps.slice(3).map(([key, label]) => {
    // Audio extraction is part of the transcription branch; it is not an extra pipeline step.
    const tasks = item.stages?.filter((task) => task.stage.toLowerCase() === key
      || (key === "video_transcribe" && task.stage.toLowerCase() === "video_extract_audio")) ?? [];
    let state: StepState;
    if (tasks.length) state = combine(tasks.map(stageState));
    else if (key === "content_enrich") state = stageState({ status: item.content_enrich.status,
      error: item.content_enrich.error ? { class: "task_error", summary: item.content_enrich.error } : null });
    else if (key === "media_download") state = combine([branchState(item.image_download), branchState(item.video_download)]);
    else if (key === "video_transcribe") state = branchState(item.video_transcription);
    else if (key === "card_finalize") state = stageState(item.finalize);
    else state = "unknown";
    if (stopped && !hasItemEvidence(item, key)) state = "not_executed";
    if (state === "failed" || state === "blocked") stopped = true;
    return { key, label, state, complete: state === "complete" ? 1 : 0, total: 1,
      failed: state === "failed" || state === "blocked" ? 1 : 0 };
  });
}
export function traceInterruptions(trace: SourceRunTrace): Interruption[] {
  const result: Interruption[] = [];
  const run = trace.run;
  const fetch: TraceStage = { task_id: run.root_task_id, stage: "fetch", status: run.execution_status ?? "unavailable",
    attempt: run.attempt_count, max_attempts: run.max_attempts ?? null, retry_at: run.retry_at, error: run.error };
  if (isInterruption(stageState(fetch))) result.push({ key: "fetch", label: "抓取", task: fetch });
  for (const item of trace.items) {
    const persisted = item.stages ?? [];
    const fallback: TraceStage[] = [];
    if (!persisted.some((s) => s.stage.toLowerCase() === "content_enrich")) fallback.push({
      task_id: item.content_enrich.task_id, stage: "content_enrich", status: item.content_enrich.status,
      attempt: item.content_enrich.attempt, max_attempts: item.content_enrich.max_attempts,
      error: item.content_enrich.error ? { class: "task_error", summary: item.content_enrich.error } : null,
    });
    for (const [key, branch] of [["media_download", item.image_download], ["media_download", item.video_download],
      ["video_transcribe", item.video_transcription]] as const) {
      for (const task of branch.tasks) {
        if (!persisted.some((s) => s.task_id === task.task_id)) fallback.push({ ...task, stage: key,
          attempt: null, max_attempts: null, error: task.error_summary ? { class: "task_error", summary: task.error_summary } : null });
      }
    }
    if (!persisted.some((s) => s.stage.toLowerCase() === "card_finalize")) fallback.push({
      task_id: item.finalize.task_id, stage: "card_finalize", status: item.finalize.status, attempt: null, max_attempts: null,
    });
    for (const task of [...persisted, ...fallback]) {
      if (!isInterruption(stageState(task))) continue;
      const key = task.stage.toLowerCase() === "video_extract_audio" ? "video_transcribe" : task.stage.toLowerCase();
      if (!pipelineSteps.some(([step]) => step === key)) continue;
      if (result.some((entry) => entry.task.task_id === task.task_id)) continue;
      result.push({ key: key as StepKey, label: stageLabel(key), itemId: rawItemId(item), title: item.title, task });
    }
  }
  return result.sort((a, b) => pipelineSteps.findIndex(([key]) => key === a.key) - pipelineSteps.findIndex(([key]) => key === b.key));
}
export function tracePipeline(trace: SourceRunTrace): PipelineStep[] {
  const fetchState = stageState({ status: trace.run.execution_status ?? "unavailable", error: trace.run.error });
  let stopped = false;
  return pipelineSteps.map(([key, label]) => {
    let states: StepState[];
    // A fetch that ran (even if it then failed or awaits retry) has left the queue.
    if (key === "queue") states = [trace.run.started_at || ["complete", "running", "failed", "blocked", "retrying"].includes(fetchState)
      ? "complete" : trace.run.execution_status === "pending" ? "pending" : "unknown"];
    else if (key === "fetch") states = [fetchState];
    else if (key === "discovery") states = [trace.discovery.availability === "available" ? "complete" : "unknown"];
    else states = trace.items.map((item) => itemPipeline(item, fetchState === "failed" || fetchState === "blocked").find((step) => step.key === key)!.state);
    if (!states.length) states = [trace.discovery.pending_items === 0 && fetchState === "complete" ? "na" : "unknown"];
    let state = combine(states);
    if (stopped && state === "unknown" && (key === "discovery"
      ? trace.discovery.availability === "unavailable" : !trace.items.some((item) => hasItemEvidence(item, key)))) {
      state = "not_executed";
    }
    if (state === "failed" || state === "blocked") stopped = true;
    return { key, label, state, total: pipelineSteps.findIndex(([step]) => step === key) > 2 ? trace.items.length : states.length,
      complete: states.filter((s) => s === "complete").length,
      failed: states.filter((s) => s === "failed" || s === "blocked").length };
  });
}
export function fullPipelineComplete(trace: SourceRunTrace): boolean {
  return !trace.item_page.truncated && !trace.item_page.next_cursor
    && trace.run.completeness_status !== "partial"
    && trace.run.card_progress.total !== null && trace.run.card_progress.complete !== null
    && trace.run.card_progress.complete === trace.run.card_progress.total
    && tracePipeline(trace).every((step) => ["complete", "skipped", "na"].includes(step.state));
}
export function pipelineDuration(trace: SourceRunTrace): number | null {
  const tasks = trace.items.flatMap((item) => item.stages ?? []).filter((task) =>
    pipelineSteps.some(([key]) => key === task.stage.toLowerCase()) || task.stage.toLowerCase() === "video_extract_audio");
  if (!trace.run.started_at || !trace.run.finished_at || tasks.some((task) => !task.finished_at)) return null;
  const start = Date.parse(trace.run.started_at);
  const end = Math.max(Date.parse(trace.run.finished_at), ...tasks.map((task) => Date.parse(task.finished_at!)));
  return Number.isFinite(start) && Number.isFinite(end) && end >= start ? end - start : null;
}
export function listPipeline(run: SourceRunListItem): { states: StepState[]; interruption: string | null } {
  const buckets = run.pipeline?.buckets ?? [];
  const hasRetry = run.issues?.some((issue) => issue.code === "retry_scheduled" && issue.count > 0);
  let fetch = stageState({ status: run.execution_status ?? "unavailable" });
  if (run.blocking_reason && fetch !== "complete") fetch = "blocked";
  else if (fetch === "pending" && hasRetry && (run.attempt_count ?? 0) > 1) fetch = "retrying";
  const stats = run.fetch_result;
  const progress = run.card_progress;
  const stopped = fetch === "failed" || fetch === "blocked";
  const stored: StepState = stopped && !stats.raw_item_count ? "not_executed"
    : stats.raw_item_count !== null ? (fetch === "complete" ? "complete" : "unknown") : "unknown";
  const downstream = buckets.filter((bucket) => bucket.bucket !== "fetch");
  const count = (status: string) => downstream.some((bucket) => (bucket.counts[status] ?? 0) > 0);
  const cards: StepState = run.blocking_reason && fetch === "complete" || count("blocked") ? "blocked"
    : progress.failed || count("failed") ? "failed"
    : fetch === "complete" && hasRetry ? "retrying" : count("running") ? "running"
    : stopped && !downstream.some((bucket) => bucket.counts.total > 0) && !progress.total ? "not_executed"
    : progress.total === 0 ? "na"
    : progress.total !== null && progress.complete === progress.total ? "complete"
    : progress.partial ? "unknown" : progress.total !== null ? "pending" : "unknown";
  return { states: [fetch, stored, cards], interruption: isInterruption(fetch)
    ? "抓取" : isInterruption(cards) ? "卡片" : null };
}
