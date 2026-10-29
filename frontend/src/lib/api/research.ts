import { errorField, sendJson, HttpRequestError } from "./http";
import type { ResearchPlatformCapability, ResearchRunDetail, ResearchRunListResult, ResearchRunRequest, ResearchRunSubmitResult } from "../types/research";

export class ResearchApiError extends HttpRequestError {
  constructor(message: string, status: number) {
    super("http", message, status);
  }
}

type CatalogPlatform = Omit<ResearchPlatformCapability, "research"> & { configurable: boolean; research: ResearchPlatformCapability["research"] | null };

export async function fetchResearchCapabilities(signal?: AbortSignal): Promise<ResearchPlatformCapability[]> {
  const payload = await requestJson<{ platforms: CatalogPlatform[] }>("/api/platform-capabilities", "GET", undefined, signal);
  return payload.platforms.filter((platform): platform is CatalogPlatform & ResearchPlatformCapability => platform.configurable && platform.research !== null);
}

export function fetchResearchRuns(signal?: AbortSignal): Promise<ResearchRunListResult> {
  return requestJson<ResearchRunListResult>("/api/research-runs?limit=20", "GET", undefined, signal);
}

export function fetchResearchRun(researchRunId: string, cursor?: string, signal?: AbortSignal): Promise<ResearchRunDetail> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return requestJson<ResearchRunDetail>(`/api/research-runs/${encodeURIComponent(researchRunId)}${query}`, "GET", undefined, signal);
}

export function createResearchRun(body: ResearchRunRequest, idempotencyKey: string): Promise<ResearchRunSubmitResult> {
  return requestJson<ResearchRunSubmitResult>("/api/research-runs", "POST", body, undefined, { "Idempotency-Key": idempotencyKey });
}

async function requestJson<T>(path: string, method: "GET" | "POST", body?: unknown, signal?: AbortSignal, headers?: Record<string, string>): Promise<T> {
  return sendJson<T>(path, method, body, {
    // GET 和写请求原先均直接 fetch；null 显式保留不设超时的语义。
    timeoutMs: null,
    signal, headers,
    httpError: (status, payload) => new ResearchApiError(
      errorField(payload, "message") ?? `请求失败（${status}）`, status,
    ),
  });
}
