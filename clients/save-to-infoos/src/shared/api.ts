import { apiUrl, hasUsableCapabilities } from "./config";
import type {
  CaptureCapabilities,
  CaptureReceipt,
  CaptureRequest
} from "./types";

export type CaptureApiErrorKind =
  | "unauthorized"
  | "forbidden"
  | "too_large"
  | "rejected"
  | "server_error"
  | "timeout"
  | "network"
  | "invalid_response";

export class CaptureApiError extends Error {
  constructor(
    public readonly kind: CaptureApiErrorKind,
    public readonly safeCode?: string,
    public readonly httpStatus?: number
  ) {
    super(kind);
  }
}

interface ApiOptions {
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
}

function withTimeout(timeoutMs: number): {
  signal: AbortSignal;
  dispose: () => void;
} {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  return {
    signal: controller.signal,
    dispose: () => clearTimeout(timeoutId)
  };
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function safeErrorCode(value: unknown): string | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  const code = (value as { code?: unknown }).code;
  return typeof code === "string" && /^[a-zA-Z0-9_.-]{1,80}$/.test(code)
    ? code
    : undefined;
}

function apiErrorFromResponse(response: Response, payload: unknown): CaptureApiError {
  const safeCode = safeErrorCode(payload);
  if (response.status === 401) {
    return new CaptureApiError("unauthorized", safeCode, response.status);
  }
  if (response.status === 403) {
    return new CaptureApiError("forbidden", safeCode, response.status);
  }
  if (response.status === 413) {
    return new CaptureApiError("too_large", safeCode, response.status);
  }
  if (response.status >= 500) {
    return new CaptureApiError("server_error", safeCode, response.status);
  }
  return new CaptureApiError("rejected", safeCode, response.status);
}

async function performRequest(
  input: RequestInfo | URL,
  init: RequestInit,
  options: ApiOptions
): Promise<Response> {
  const timeout = withTimeout(options.timeoutMs ?? 15_000);
  try {
    return await (options.fetchImpl ?? fetch)(input, {
      ...init,
      signal: timeout.signal
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new CaptureApiError("timeout");
    }
    throw new CaptureApiError("network");
  } finally {
    timeout.dispose();
  }
}

function isCapabilities(value: unknown): value is CaptureCapabilities {
  if (!value || typeof value !== "object") {
    return false;
  }
  return hasUsableCapabilities(value as CaptureCapabilities);
}

function isReceipt(value: unknown): value is CaptureReceipt {
  if (!value || typeof value !== "object") {
    return false;
  }
  const receipt = value as Partial<CaptureReceipt>;
  return (
    receipt.schema === "infoos.capture-receipt.v1" &&
    typeof receipt.capture_id === "string" &&
    (receipt.status === "accepted" || receipt.status === "duplicate") &&
    (receipt.disposition === "new" ||
      receipt.disposition === "refresh" ||
      receipt.disposition === "unchanged" ||
      receipt.disposition === "idempotent_replay") &&
    typeof receipt.infoos_url === "string" &&
    typeof receipt.request_id === "string"
  );
}

export async function fetchCapabilities(
  baseUrl: string,
  token: string,
  options: ApiOptions = {}
): Promise<CaptureCapabilities> {
  const response = await performRequest(
    apiUrl(baseUrl, "/api/capture/v1/capabilities"),
    {
      method: "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json"
      }
    },
    options
  );
  const payload = await safeJson(response);
  if (!response.ok) {
    throw apiErrorFromResponse(response, payload);
  }
  if (!isCapabilities(payload)) {
    throw new CaptureApiError("invalid_response");
  }
  return payload;
}

export async function submitCapture(
  baseUrl: string,
  token: string,
  request: CaptureRequest,
  options: ApiOptions = {}
): Promise<CaptureReceipt> {
  const response = await performRequest(
    apiUrl(baseUrl, "/api/capture/v1/items"),
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Idempotency-Key": request.client_capture_id
      },
      body: JSON.stringify(request)
    },
    options
  );
  const payload = await safeJson(response);
  if (!response.ok && response.status !== 409) {
    throw apiErrorFromResponse(response, payload);
  }
  if (!isReceipt(payload)) {
    throw new CaptureApiError("invalid_response", safeErrorCode(payload), response.status);
  }
  return payload;
}
