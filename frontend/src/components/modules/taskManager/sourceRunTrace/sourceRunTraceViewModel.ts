import type { MediaBranch, SourceRunStatus, SourceRunTraceItem, TaskQueueName } from "../../../../lib/types/sourceRuns";
import { runStatusLabel } from "../../../../lib/labels";

const queueLabels: Record<string, string> = {
  public_fetch: "公开采集",
  browser_fetch: "浏览器采集",
  urgent_media: "紧急媒体",
  card_pipeline: "卡片流水线",
  gpu_asr: "GPU 转录",
  cloud_ai: "云端 AI",
  maintenance_io: "维护与 I/O",
};

export function queueLabel(queue: TaskQueueName | null | undefined): string {
  return queue ? queueLabels[queue] ?? queue : "未分配队列";
}

export function fact(value: number | null | undefined): string {
  return value === null || value === undefined ? "不可用" : String(value);
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "不可用";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} 秒`;
  return `${Math.floor(ms / 60_000)} 分 ${Math.round((ms % 60_000) / 1000)} 秒`;
}

export function waitDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  return `${Math.floor(seconds / 3600)} 时 ${Math.floor((seconds % 3600) / 60)} 分`;
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return "尚未开始";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value));
}

export function statusLabel(status: SourceRunStatus): string {
  return runStatusLabel(status);
}

export function branchSummary(branch: MediaBranch): string {
  if (branch.availability === "unavailable") return "证据不可用";
  return `总数 ${fact(branch.total)} · 成功 ${fact(branch.success)} · 失败 ${fact(branch.failed)} · 重试 ${fact(branch.retry_count)}`;
}

export function rawItemId(item: SourceRunTraceItem): string {
  return item.raw_item_id;
}

export function cardId(item: SourceRunTraceItem): string {
  return item.card?.id ?? item.card_id ?? "尚未构卡";
}
