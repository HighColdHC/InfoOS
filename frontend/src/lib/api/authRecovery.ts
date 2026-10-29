import { errorField, requestJsonResponse, HttpRequestError, type HttpIssueKind } from "./http";
import type {
  AuthRecoveryResponse,
  OpenAuthRecoveryConsoleResponse,
  VerifyAuthRecoveryResponse,
} from "../types/authRecovery";

export class AuthRecoveryApiError extends HttpRequestError {
  constructor(message: string, status: number | undefined, kind: HttpIssueKind = "http") {
    super(kind, message, status);
  }
}

export function fetchAuthRecovery(incidentId: string, signal?: AbortSignal): Promise<AuthRecoveryResponse> {
  return request(`/api/auth-recoveries/${encodeURIComponent(incidentId)}`, { signal });
}

export function openAuthRecoveryConsole(incidentId: string): Promise<OpenAuthRecoveryConsoleResponse> {
  return request(`/api/auth-recoveries/${encodeURIComponent(incidentId)}/open-console`, { method: "POST" });
}

export function openPendingAuthRecoveryConsole(url: string): Promise<OpenAuthRecoveryConsoleResponse> {
  return request(url, { method: "POST" });
}

export function verifyAuthRecovery(incidentId: string): Promise<VerifyAuthRecoveryResponse> {
  return request(`/api/auth-recoveries/${encodeURIComponent(incidentId)}/verify`, { method: "POST" });
}

export function verifyPendingAuthRecovery(url: string): Promise<VerifyAuthRecoveryResponse> {
  return request(url, { method: "POST" });
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  try {
    const { payload, status } = await requestJsonResponse<T>(path, init, {
      // GET 和写请求原先均直接 fetch；null 显式保留不设超时的语义。
      timeoutMs: null,
      httpError: (status, payload) => new AuthRecoveryApiError(
        errorField(payload, "message") ?? "恢复请求暂时无法完成，请稍后重试。", status,
      ),
    });
    if (!payload) throw new AuthRecoveryApiError("恢复服务返回无效响应，请稍后重试。", status, "parse");
    return payload;
  } catch (error) {
    if (error instanceof AuthRecoveryApiError) throw error;
    if (error instanceof HttpRequestError) {
      throw new AuthRecoveryApiError(error.kind === "parse"
        ? "恢复服务返回无效响应，请稍后重试。" : "恢复请求暂时无法完成，请稍后重试。", error.status, error.kind);
    }
    throw error;
  }
}
