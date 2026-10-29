import { errorField, getJson, requestJsonResponse, HttpRequestError } from "./http";
import type { BrowserActionResult, BrowserConsoleActionResult, CollectionEnvironmentSnapshot } from "../types/collectionEnvironment";

export function fetchCollectionEnvironment(signal?: AbortSignal): Promise<CollectionEnvironmentSnapshot> {
  return getJson<CollectionEnvironmentSnapshot>("/api/collection-environment", signal);
}

export function launchDedicatedChrome(): Promise<BrowserConsoleActionResult> {
  return postAction<BrowserConsoleActionResult>("/api/browser/session", { action: "launch" });
}

export function checkPlatformAccount(platform: string): Promise<BrowserActionResult> {
  return postAction("/api/browser/login-check", { platform }, { allowNegativeResult: true });
}

export function launchPlatformLogin(platform: string): Promise<BrowserConsoleActionResult> {
  return postAction<BrowserConsoleActionResult>("/api/browser/login-launch", { platform });
}

async function postAction<T extends BrowserActionResult>(path: string, body: Record<string, string>, options: { allowNegativeResult?: boolean } = {}): Promise<T> {
  const { payload, status } = await requestJsonResponse<T>(path, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  }, { httpError: actionError });
  if (payload.ok === false && !options.allowNegativeResult) throw actionError(status, payload);
  return payload;
}

function actionError(status: number, payload: unknown): HttpRequestError {
  const message = errorField(payload, "message") ?? errorField(payload, "error") ?? `请求失败（${status}）`;
  return new HttpRequestError("http", message, status);
}
