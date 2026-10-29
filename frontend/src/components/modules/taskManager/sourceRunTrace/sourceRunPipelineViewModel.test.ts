import { describe, expect, it } from "vitest";
import type { SourceRunTrace, TraceStage } from "../../../../lib/types/sourceRuns";
import { traceFixture } from "./__tests__/sourceRunTraceTestFixtures";
import { fullPipelineComplete, itemPipeline, listPipeline, pipelineDuration, stageLabel, stageState, traceInterruptions, tracePipeline } from "./sourceRunPipelineViewModel";

function runWith(stages: TraceStage[]): SourceRunTrace {
  const item = { ...traceFixture.items[0], stages, video_download: { ...traceFixture.items[0].video_download, failed: 0, tasks: [] } };
  return { ...traceFixture, run: { ...traceFixture.run, execution_status: "success", error: null,
    max_attempts: 3, retry_at: null, card_progress: { total: 1, complete: 1, partial: 0, failed: 0 } },
    items: [item], item_page: { items: [item], item_total: 1, truncated: false, next_cursor: null } };
}
const stages: TraceStage[] = ["card_build", "content_enrich", "media_download", "video_transcribe", "card_finalize"]
  .map((stage) => ({ task_id: `task-${stage}`, stage, status: "success", attempt: 1, max_attempts: 3, error: null }));
const failure = { class: "network_timeout", summary: "下载超时" };

describe("来源运行处理流程", () => {
  it("根 FETCH 失败使用根错误及尝试上限，不推测受影响篇数", () => {
    const trace = runWith(stages);
    trace.run = { ...trace.run, execution_status: "failed", error: failure, attempt_count: 3 };
    const [stop] = traceInterruptions(trace);
    expect(stop).toMatchObject({ key: "fetch", label: "抓取", task: { error: failure, attempt: 3, max_attempts: 3 } });
    expect(stop.itemId).toBeUndefined();
    expect(tracePipeline({ ...trace, run: { ...trace.run, started_at: null } }).find((s) => s.key === "queue")?.state).toBe("complete");
    expect(fullPipelineComplete(trace)).toBe(false);
  });
  it("媒体等待重试仍突出中断，保留下次时间且不归因于已成功的抓取", () => {
    const trace = runWith(stages.map((s) => s.stage === "media_download" ? { ...s, status: "pending", error: failure,
      attempt: 2, retry_at: "2026-10-21T09:30:00Z" } : s));
    expect(traceInterruptions(trace)).toEqual([expect.objectContaining({ key: "media_download", itemId: "raw-1",
      task: expect.objectContaining({ attempt: 2, retry_at: "2026-10-21T09:30:00Z" }) })]);
    expect(tracePipeline(trace).find((s) => s.key === "fetch")?.state).toBe("complete");
    expect(tracePipeline(trace).find((s) => s.key === "media_download")).toMatchObject({ state: "retrying", failed: 0 });
    trace.items[0].stages = trace.items[0].stages!.map((stage) => stage.stage === "media_download"
      ? { ...stage, status: "failed", retry_at: null } : stage);
    expect(tracePipeline(trace).find((s) => s.key === "media_download")).toMatchObject({ state: "failed", failed: 1 });
  });
  it("转录阻塞和音轨提取失败归入转录步骤", () => {
    const trace = runWith(stages.map((s) => s.stage === "video_transcribe" ? { ...s, status: "blocked", error: failure } : s));
    expect(traceInterruptions(trace)[0]).toMatchObject({ key: "video_transcribe", label: "转录" });
    expect(tracePipeline(trace)[6].state).toBe("blocked");
    trace.items[0].stages = [{ ...stages[3], stage: "video_extract_audio", status: "failed", error: failure }];
    expect(traceInterruptions(trace)[0].label).toBe("转录");
  });
  it("全成功须有全部阶段与完整分页证据，缺证据不冒充成功", () => {
    const trace = runWith(stages);
    expect(traceInterruptions(trace)).toEqual([]);
    expect(fullPipelineComplete(trace)).toBe(true);
    expect(fullPipelineComplete({ ...trace, run: { ...trace.run,
      card_progress: { total: null, complete: null, partial: null, failed: null } } })).toBe(false);
    expect(fullPipelineComplete({ ...trace, item_page: { ...trace.item_page, next_cursor: "more", truncated: true } })).toBe(false);
    trace.items[0].stages = stages.filter((s) => s.stage !== "card_build");
    expect(fullPipelineComplete(trace)).toBe(false);
    expect(itemPipeline(trace.items[0])[0].state).toBe("unknown");
  });
  it("未加载条目不会推导为无媒体或全成功", () => {
    const trace = runWith(stages);
    trace.items = [];
    expect(fullPipelineComplete(trace)).toBe(false);
    expect(tracePipeline(trace)[3].state).toBe("unknown");
  });
  it("跳过、进行中、初次排队不误报中断", () => {
    const trace = runWith(stages.map((s, index) => ({ ...s, status: ["success", "running", "pending", "skipped", "pending"][index] })));
    expect(traceInterruptions(trace)).toEqual([]);
    expect(itemPipeline(trace.items[0]).map((s) => s.state)).toEqual(["complete", "running", "pending", "skipped", "pending"]);
  });
  it("聚合按篇计数，同篇多个失败只计一篇且保留各任务错误", () => {
    const trace = runWith([...stages, { ...stages[2], task_id: "other-media", status: "failed", error: failure },
      { ...stages[2], task_id: "third-media", status: "failed", error: failure }]);
    expect(traceInterruptions(trace)).toHaveLength(2);
    expect(tracePipeline(trace)[5]).toMatchObject({ total: 1, failed: 1 });
  });
  it("旧响应缺 stages 时仍能读取已有分支错误，缺失的次数保持未知", () => {
    const stop = traceInterruptions(traceFixture)[0];
    expect(stop).toMatchObject({ label: "媒体下载", task: { task_id: "task-video-1", attempt: null, max_attempts: null } });
    expect(fullPipelineComplete(traceFixture)).toBe(false);
  });
  it("总耗时覆盖根任务开始至最后阶段完成，缺完整时间证据时返回未知", () => {
    const trace = runWith(stages.map((s) => ({ ...s, finished_at: "2026-07-16T08:03:00Z" })));
    trace.run.finished_at = "2026-07-16T08:01:00Z";
    expect(pipelineDuration(trace)).toBe(180000);
    trace.items[0].stages!.push({ task_id: "optional-webhook", stage: "webhook_deliver", status: "pending",
      attempt: 1, max_attempts: 3, finished_at: null });
    expect(pipelineDuration(trace)).toBe(180000);
    trace.items[0].stages![0].finished_at = null;
    expect(pipelineDuration(trace)).toBeNull();
  });
  it("阶段名称中文映射与不适用状态", () => {
    expect(stageLabel("CONTENT_ENRICH")).toBe("正文富化");
    const trace = runWith(stages.filter((s) => s.stage !== "video_transcribe"));
    trace.items[0].video_transcription = { total: 0, success: 0, failed: 0, retry_count: 0, tasks: [] };
    expect(itemPipeline(trace.items[0])[3].state).toBe("na");
  });
  it("列表用根执行事实判抓取，用卡片进度判卡片失败", () => {
    const base = { ...traceFixture.run, fetch_result: traceFixture.run.fetch_stats, duration_ms: 10,
      execution_status: "success", card_progress: { total: 1, complete: 0, partial: 0, failed: 1 } };
    expect(listPipeline(base)).toEqual({ states: ["complete", "complete", "failed"], interruption: "卡片" });
    expect(listPipeline({ ...base, execution_status: "failed" }).interruption).toBe("抓取");
    expect(listPipeline({ ...base, blocking_reason: "task_blocked" })).toEqual({
      states: ["complete", "complete", "blocked"], interruption: "卡片" });
    expect(listPipeline({ ...base, execution_status: "pending", blocking_reason: "platform_not_logged_in" }).interruption).toBe("抓取");
  });
});

describe("r4c 重试及下游未执行", () => {
  it("初次排队与待重试分别呈现，聚合优先级为 blocked > failed > retrying > running > pending", () => {
    expect(stageState({ status: "pending", error: failure })).toBe("retrying");
    expect(stageState({ status: "pending", error: null })).toBe("pending");
    const variants = ["blocked", "failed", "pending", "running", "pending"];
    for (let offset = 0; offset < variants.length; offset++) {
      const trace = runWith(variants.slice(offset).map((status, index) => ({ ...stages[2], task_id: `priority-${index}`,
        status, error: offset + index === 2 ? failure : null })));
      expect(tracePipeline(trace)[5].state).toBe(["blocked", "failed", "retrying", "running", "pending"][offset]);
    }
  });
  it("根抓取失败后无下游证据为未执行，已有任务证据继续保留", () => {
    const trace = runWith([]);
    trace.run = { ...trace.run, execution_status: "failed", error: failure };
    trace.discovery = { ...trace.discovery, availability: "unavailable" };
    trace.items = [];
    expect(tracePipeline(trace).slice(2).map((step) => step.state)).toEqual(Array(6).fill("not_executed"));
    trace.items = runWith(stages).items;
    expect(tracePipeline(trace).slice(3).map((step) => step.state)).toEqual(Array(5).fill("complete"));
  });
  it("逐篇首个失败之后只把无任务证据的步骤标未执行；上游成功时缺证据仍未知", () => {
    const item = { ...runWith([{ ...stages[0], status: "blocked" }]).items[0],
      content_enrich: { ...traceFixture.items[0].content_enrich, task_id: "unavailable", status: "unavailable" },
      image_download: { total: 0, success: 0, failed: 0, retry_count: 0, tasks: [] },
      video_download: { total: 0, success: 0, failed: 0, retry_count: 0, tasks: [] },
      video_transcription: { total: 0, success: 0, failed: 0, retry_count: 0, tasks: [] },
      finalize: { ...traceFixture.items[0].finalize, task_id: "unavailable", status: "unavailable" } };
    expect(itemPipeline(item).map((step) => step.state)).toEqual(["blocked", ...Array(4).fill("not_executed")]);
    expect(itemPipeline({ ...item, stages: [stages[0]] })[1].state).toBe("unknown");
    expect(itemPipeline({ ...item, stages: [{ ...stages[0], status: "blocked" }, { ...stages[2], status: "pending" }] })[2].state).toBe("pending");
  });
  it("列表使用已有 issue 与 bucket 区分重试、失败和阻塞，抓取失败下游未执行", () => {
    const run = { ...runWith(stages).run, fetch_result: traceFixture.run.fetch_stats,
      issues: [{ code: "retry_scheduled", count: 1 }] };
    expect(listPipeline(run)).toEqual({ states: ["complete", "complete", "retrying"], interruption: "卡片" });
    expect(listPipeline({ ...run, execution_status: "pending", attempt_count: 2 }).states[0]).toBe("retrying");
    expect(listPipeline({ ...run, execution_status: "pending", attempt_count: 0 }).states[0]).toBe("pending");
    expect(listPipeline({ ...run, pipeline: { buckets: [{ bucket: "media", counts: { total: 2, failed: 1 } }] } }).states[2]).toBe("failed");
    expect(listPipeline({ ...run, pipeline: { buckets: [{ bucket: "media", counts: { total: 2, blocked: 1, failed: 1 } }] } }).states[2]).toBe("blocked");
    expect(listPipeline({ ...run, execution_status: "failed", fetch_result: { total_links: null, dedup_skipped: null,
      pending_items: null, raw_item_count: null }, card_progress: { total: null, complete: null, partial: null, failed: null } }).states)
      .toEqual(["failed", "not_executed", "not_executed"]);
  });
});
