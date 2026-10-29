import { errorField, getJson, postJson as sendPostJson, HttpRequestError } from "./http";
import type {
  ExportEstimate, ExportJobDetail, ExportJobPage, ExportRequest, ExportSubmitResult,
  OperationLog, OperationsLogPage, OperationsLogQuery,
} from "../types/exportLogs";

export function estimateExport(request: ExportRequest): Promise<ExportEstimate> {
  return postJson<ExportEstimate>("/api/exports/estimate", request);
}

export function submitExport(request: ExportRequest): Promise<ExportSubmitResult> {
  return postJson<ExportSubmitResult>("/api/exports", request);
}

export function fetchExportJobs(query: { kind?: string; status?: string; limit?: number; offset?: number } = {}, signal?: AbortSignal): Promise<ExportJobPage> {
  const params = new URLSearchParams({ limit: String(query.limit ?? 25), offset: String(query.offset ?? 0) });
  if (query.kind) params.set("kind", query.kind); if (query.status) params.set("status", query.status);
  return getJson<ExportJobPage>(`/api/exports?${params}`, signal);
}

export function fetchExportJob(taskId: string, signal?: AbortSignal): Promise<ExportJobDetail> {
  return getJson<ExportJobDetail>(`/api/exports/${encodeURIComponent(taskId)}`, signal);
}

export function exportDownloadUrl(taskId: string): string {
  return `/api/exports/${encodeURIComponent(taskId)}/download`;
}

export function fetchOperationsLogs(query: OperationsLogQuery = {}, signal?: AbortSignal): Promise<OperationsLogPage> {
  const params = new URLSearchParams({ limit: String(query.limit ?? 25), offset: String(query.offset ?? 0) });
  if (query.query?.trim()) params.set("query", query.query.trim()); if (query.level) params.set("level", query.level); if (query.area) params.set("area", query.area);
  if (query.timeFrom) params.set("time_from", query.timeFrom); if (query.timeTo) params.set("time_to", query.timeTo); if (query.objectId) params.set("object_id", query.objectId); if (query.requestId) params.set("request_id", query.requestId);
  return getJson<OperationsLogPage>(`/api/logs?${params}`, signal);
}

export function fetchLogDetail(logId: string, signal?: AbortSignal): Promise<OperationLog> {
  return getJson<OperationLog>(`/api/logs/${encodeURIComponent(logId)}`, signal);
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  return sendPostJson<T>(path, body, {
    httpError: (status, payload) => new HttpRequestError("http", errorField(payload, "message") ?? `请求失败（${status}）`, status),
  });
}
