import { errorField, requestJson, HttpRequestError } from "./http";
import type {
  RequestLogDetail, RequestLogPage, RequestLogQuery, TokenAdminRecord,
  TokenConfigurationInput, TokenCreateResult, TokenOverview, TokenScope,
  WebhookReplayResult, WebhookTestResult,
} from "../types/tokenAuth";

export function fetchTokenOverview(signal?: AbortSignal): Promise<TokenOverview> {
  return request<TokenOverview>("/api/token-auth/overview", { signal });
}

export async function fetchAdminTokens(signal?: AbortSignal): Promise<TokenAdminRecord[]> {
  const payload = await request<{ items?: TokenAdminRecord[]; tokens?: TokenAdminRecord[] }>("/api/token-auth/tokens", { signal });
  return payload.items ?? payload.tokens ?? [];
}

export function createAdminToken(input: TokenConfigurationInput): Promise<TokenCreateResult> {
  return request<TokenCreateResult>("/api/token-auth/tokens", json("POST", input));
}

export function updateAdminToken(tokenId: string, input: TokenConfigurationInput): Promise<TokenAdminRecord> {
  return request<TokenAdminRecord>(`/api/token-auth/tokens/${encodeURIComponent(tokenId)}`, json("PATCH", input));
}

export async function revokeAdminToken(tokenId: string): Promise<TokenAdminRecord> {
  const payload = await request<TokenAdminRecord | { token: TokenAdminRecord }>(`/api/token-auth/tokens/${encodeURIComponent(tokenId)}/revoke`, json("POST", {}));
  return "token" in payload ? payload.token : payload;
}

export function rotateAdminToken(tokenId: string): Promise<TokenCreateResult> {
  return request<TokenCreateResult>(`/api/token-auth/tokens/${encodeURIComponent(tokenId)}/rotate`, json("POST", {}));
}

export function testAdminTokenWebhook(tokenId: string): Promise<WebhookTestResult> {
  return request<WebhookTestResult>(
    `/api/token-auth/tokens/${encodeURIComponent(tokenId)}/webhook-test`,
    json("POST", {}),
  );
}

export function replayAdminTokenWebhooks(tokenId: string, limit = 50): Promise<WebhookReplayResult> {
  return request<WebhookReplayResult>(
    `/api/token-auth/tokens/${encodeURIComponent(tokenId)}/webhook-replay`,
    json("POST", { limit }),
  );
}

export function fetchRequestLogs(filters: RequestLogQuery = {}, signal?: AbortSignal): Promise<RequestLogPage> {
  const params = new URLSearchParams();
  const query = filters.query?.trim();
  if (query) params.set("query", query);
  if (filters.status) params.set("status", filters.status);
  params.set("time_range", filters.time_range || "24h");
  params.set("limit", String(filters.limit ?? 25));
  params.set("offset", String(filters.offset ?? 0));
  return request<RequestLogPage>(`/api/token-auth/request-logs?${params.toString()}`, { signal });
}

export function fetchRequestLogDetail(requestId: string, signal?: AbortSignal): Promise<RequestLogDetail> {
  return request<RequestLogDetail>(`/api/token-auth/request-logs/${encodeURIComponent(requestId)}`, { signal });
}

export class TokenAuthApiError extends HttpRequestError {
  constructor(message: string, status: number) {
    super("http", message, status);
    this.name = "TokenAuthApiError";
  }
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  return requestJson<T>(path, init, {
    // GET 和写请求原先均直接 fetch；null 显式保留不设超时的语义。
    timeoutMs: null,
    httpError: (status, payload) => {
      const code = errorField(payload, "code");
      const message = errorField(payload, "message");
      return code !== undefined && message !== undefined
        ? new TokenAuthApiError(message, status)
        : new TokenAuthApiError(`请求失败（${status}）`, status);
    },
  });
}

function json(method: "POST" | "PATCH", body: unknown): RequestInit {
  return { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}
