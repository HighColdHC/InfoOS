import { errorField, getJson, postJson, HttpRequestError } from "./http";
import type { RetryTaskResult, SourceRunListPage, SourceRunQuery, SourceRunTrace, TaskTrackingPage } from "../types/sourceRuns";

export function fetchSourceRuns(query: SourceRunQuery = {}, signal?: AbortSignal): Promise<SourceRunListPage> {
  const params = new URLSearchParams();
  if (query.query?.trim()) params.set("query", query.query.trim());
  if (query.platform) params.set("platform", query.platform);
  if (query.status) params.set("status", query.status);
  if (query.executionScope) params.set("execution_scope", query.executionScope);
  if (query.taskView) params.set("task_view", query.taskView);
  if (query.timeFrom) params.set("time_from", query.timeFrom);
  if (query.timeTo) params.set("time_to", query.timeTo);
  params.set("limit", String(query.limit ?? 15));
  params.set("offset", String(query.offset ?? 0));
  return getJson<SourceRunListPage>(`/api/source-runs?${params}`, signal);
}

export function fetchSourceRunTrace(sourceRunId: string, signal?: AbortSignal, cursor?: string): Promise<SourceRunTrace> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  const query = params.size ? `?${params}` : "";
  return getJson<SourceRunTrace>(`/api/source-runs/${encodeURIComponent(sourceRunId)}/trace${query}`, signal);
}

export function fetchTaskTrackingPage(
  query: { limit?: number; offset?: number; queueName?: string; status?: string } = {},
  signal?: AbortSignal,
): Promise<TaskTrackingPage> {
  const params = new URLSearchParams({
    limit: String(query.limit ?? 20),
    offset: String(query.offset ?? 0),
  });
  if (query.queueName) params.set("queue_name", query.queueName);
  if (query.status) params.set("status", query.status);
  return getJson<TaskTrackingPage>(`/api/task-queue-tracking?${params}`, signal);
}

export async function retrySourceRunTask(taskId: string): Promise<RetryTaskResult> {
  const payload = await postJson<{ task: RetryTaskResult }>(`/api/tasks/${encodeURIComponent(taskId)}/retry`, {}, {
    httpError: (status, payload) => new HttpRequestError(
      "http", errorField(payload, "message") ?? errorField(payload, "error") ?? `重试请求失败（${status}）`, status,
    ),
  });
  return payload.task;
}

export function cancelSourceRunTask(taskId: string, reason: string): Promise<{ task_id: string; status: string }> {
  return postJson(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { reason }, {
    httpError: (status, payload) => new HttpRequestError(
      "http", errorField(payload, "message") ?? `取消请求失败（${status}）`, status,
    ),
  });
}
