import type { ExportKind, ExportRequest } from "../../../../lib/types/exportLogs";

export function exportPreset(kind: ExportKind): ExportRequest {
  const base: ExportRequest = { kind, format: kind === "data" ? "json" : "zip", time_from: null, time_to: null, include_sources_runs: false, include_cards_content: false, include_asset_index: true, include_attachment_files: false, include_redacted_logs: false, include_media_files: false, log_levels: [], include_environment_snapshot: false, source_run_ids: [] };
  if (kind === "data") return { ...base, include_sources_runs: true, include_cards_content: true };
  if (kind === "backup") return base;
  return { ...base, include_redacted_logs: true, include_environment_snapshot: true };
}

export function exportRequestForTransport(request: ExportRequest): ExportRequest {
  const normalizeTime = (value: string | null) => value ? new Date(value).toISOString() : null;
  return { ...request, time_from: normalizeTime(request.time_from), time_to: normalizeTime(request.time_to) };
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`; if (value < 1024 ** 2) return `${Math.round(value / 1024)} KB`; if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`; return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

export function statusLabel(status: string): string {
  return ({ pending: "排队中", running: "生成中", completed: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[status] ?? status;
}

export function updateTaskUrl(taskId: string | null) {
  const url = new URL(window.location.href); if (taskId) url.searchParams.set("export_task_id", taskId); else url.searchParams.delete("export_task_id"); window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}
