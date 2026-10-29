export type HttpIssueKind = "http" | "network" | "timeout" | "parse";

export class HttpRequestError extends Error {
  constructor(public readonly kind: HttpIssueKind, message: string, public readonly status?: number) {
    super(message);
    this.name = "HttpRequestError";
  }
}

// Reads a string field from an HTTP error body, which may be absent or non-JSON.
export function errorField(payload: unknown, key: string): string | undefined {
  const value = payload && typeof payload === "object" ? (payload as Record<string, unknown>)[key] : undefined;
  return typeof value === "string" ? value : undefined;
}

type HttpOptions = {
  // null 显式表示不设超时；省略时底层请求也不设超时。
  timeoutMs?: number | null;
  httpError?: (status: number, payload: unknown) => HttpRequestError;
};

type SendOptions = HttpOptions & { signal?: AbortSignal; headers?: HeadersInit };

// 仅兼容旧 http.ts 的读取默认值；迁移自 request.ts / fetch 的读取须显式传 null。
export function getJson<T>(path: string, signal?: AbortSignal, timeoutMs: number | null = 10_000, options: HttpOptions = {}): Promise<T> {
  return requestJson<T>(path, { signal }, { ...options, timeoutMs });
}

export async function requestJson<T>(path: string, init: RequestInit = {}, options: HttpOptions = {}): Promise<T> {
  return (await requestJsonResponse<T>(path, init, options)).payload;
}

// The status accompanies the parsed body for APIs with business errors in HTTP 2xx responses.
export function requestJsonResponse<T>(path: string, init: RequestInit = {}, options: HttpOptions = {}): Promise<{ payload: T; status: number }> {
  return request<T>(path, init, options, true);
}

export function sendJson<T>(path: string, method: string, payload?: unknown, options: SendOptions = {}): Promise<T> {
  return requestJson<T>(path, jsonInit(method, payload, options), options);
}

export function postJson<T>(path: string, payload?: unknown, options: SendOptions = {}): Promise<T> {
  return sendJson<T>(path, "POST", payload, options);
}

// A successful write may have no response body (e.g. browser visibility).
export async function sendEmpty(path: string, method: string, payload?: unknown, options: SendOptions = {}): Promise<void> {
  await request(path, jsonInit(method, payload, options), options, false);
}

function jsonInit(method: string, payload: unknown, options: SendOptions): RequestInit {
  const headers = new Headers(options.headers);
  if (payload !== undefined) headers.set("Content-Type", "application/json");
  return {
    method, signal: options.signal,
    ...(payload !== undefined || options.headers ? { headers, body: JSON.stringify(payload) } : {}),
  };
}

async function request<T>(path: string, init: RequestInit, options: HttpOptions, parseBody: boolean): Promise<{ payload: T; status: number }> {
  const signal = init.signal;
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  const controller = new AbortController();
  const timeout = { expired: false };
  const timer = typeof options.timeoutMs === "number"
    ? window.setTimeout(() => { timeout.expired = true; controller.abort(); }, options.timeoutMs)
    : undefined;
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  try {
    const response = await fetch(path, { ...init, signal: controller.signal });
    let payload: unknown;
    if (parseBody || !response.ok) {
      try {
        payload = await response.json();
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") throw error;
        if (response.ok) throw new HttpRequestError("parse", `${path} returned invalid JSON`, response.status);
      }
    }
    if (!response.ok) throw options.httpError?.(response.status, payload)
      ?? new HttpRequestError("http", `${path} returned ${response.status}`, response.status);
    return { payload: payload as T, status: response.status };
  } catch (error) {
    if (timeout.expired) throw new HttpRequestError("timeout", `${path} timed out`);
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (error instanceof HttpRequestError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new HttpRequestError("network", error instanceof Error ? error.message : `${path} failed`);
  } finally {
    if (timer !== undefined) window.clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
