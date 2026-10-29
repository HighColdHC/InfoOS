import { errorField, getJson, sendJson, HttpRequestError } from "./http";
import type {
  SourceConfigurationPayload,
  SourceManagementDetail,
  SourceManagementPage,
  SourceManagementResult,
  SourcePageQuery,
  SourcePlatformCapability,
  SourceRunSubmissionResult,
} from "../types/sourceManagement";

export function fetchSourcePage(filters: SourcePageQuery = {}, signal?: AbortSignal): Promise<SourceManagementPage> {
  const params = new URLSearchParams();
  const query = filters.query?.trim();
  if (query) params.set("query", query);
  if (filters.platform) params.set("platform", filters.platform);
  if (filters.status) params.set("status", filters.status);
  params.set("limit", String(filters.limit ?? 25));
  params.set("offset", String(filters.offset ?? 0));
  return readJson<SourceManagementPage>(`/api/configurable-sources?${params.toString()}`, signal);
}

export function fetchSourceDetail(sourceId: string, signal?: AbortSignal): Promise<SourceManagementDetail> {
  return readJson<SourceManagementDetail>(`/api/configurable-sources/${encodeURIComponent(sourceId)}`, signal);
}

export async function fetchSourcePlatformCapabilities(signal?: AbortSignal): Promise<SourcePlatformCapability[]> {
  const result = await readJson<{ platforms: SourcePlatformCapability[] }>("/api/platform-capabilities", signal);
  return result.platforms.filter((platform) => platform.configurable !== false);
}

export function createManagedSource(payload: SourceConfigurationPayload): Promise<SourceManagementResult> {
  return requestJson<SourceManagementResult>("/api/configurable-sources", "POST", payload);
}

export function updateManagedSource(sourceId: string, payload: SourceConfigurationPayload): Promise<SourceManagementResult> {
  return requestJson<SourceManagementResult>(`/api/configurable-sources/${encodeURIComponent(sourceId)}`, "PUT", payload);
}

export function setManagedSourceEnabled(sourceId: string, enabled: boolean): Promise<SourceManagementResult> {
  return requestJson<SourceManagementResult>(`/api/configurable-sources/${encodeURIComponent(sourceId)}/enabled`, "PATCH", { enabled });
}

export async function submitManagedSourceRun(sourceId: string): Promise<SourceRunSubmissionResult> {
  const payload = await requestJson<SourceRunSubmissionResult & {
    batch?: { batch_id?: string; root_task_ids?: string[]; task_ids?: string[] };
  }>(`/api/configurable-sources/${encodeURIComponent(sourceId)}/run`, "POST", {});
  if (payload.source_run_id && payload.task_id && payload.batch_id) return payload;
  const taskId = payload.batch?.root_task_ids?.[0] ?? payload.batch?.task_ids?.[0] ?? "";
  return { source_run_id: null, task_id: taskId, batch_id: payload.batch?.batch_id ?? "" };
}

function requestJson<T>(path: string, method: "POST" | "PUT" | "PATCH", body: unknown): Promise<T> {
  return sendJson<T>(path, method, body, { httpError: (status, payload) => sourceError(status, payload, true) });
}

function readJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  // 保留迁移前直接 fetch 的语义：null 表示不设超时。
  return getJson<T>(path, signal, null, { httpError: sourceError });
}

function sourceError(status: number, payload: unknown, includeExistingSource = false): SourceManagementApiError {
  return new SourceManagementApiError(
    errorField(payload, "code") ?? "request_failed",
    errorField(payload, "message") ?? `请求失败（${status}）`,
    status,
    includeExistingSource ? errorField(payload, "existing_source_id") : undefined,
  );
}

export class SourceManagementApiError extends HttpRequestError {
  constructor(
    public readonly code: string,
    message: string,
    status: number,
    public readonly existingSourceId?: string,
  ) {
    super("http", message, status);
    this.name = "SourceManagementApiError";
  }
}
